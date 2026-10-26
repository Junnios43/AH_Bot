"""
new_listings_alert.py
Alerts you when NEW costume / head & armor / accessory listings appear on sk-ah.com.

Put this file next to bid_analyzer.py and run:
    python new_listings_alert.py                  # check every 5 min, forever
    python new_listings_alert.py --once           # single check, then exit
    python new_listings_alert.py --interval 120   # check every 2 minutes
    python new_listings_alert.py --show-unmatched # print names that matched no category (for tuning)

Optional alert channels:
    - Console (always)
    - Desktop popup:  pip install plyer
    - Discord:        set env var DISCORD_WEBHOOK_URL to a channel webhook URL
"""
import argparse
import asyncio
import json
import logging
import os
import re
import urllib.request
from collections import Counter

from bid_analyzer import AuctionEvaluator  # reuses your existing scraper

logging.getLogger("AuctionEvaluator").setLevel(logging.WARNING)
log = logging.getLogger("NewListingsAlert")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

SEEN_FILE = "seen_listings.json"

# ---------------------------------------------------------------------------
# The auction site has no category column, so categories are guessed from the
# item NAME. These keyword lists are a starting point: edit them freely.
# Run with --show-unmatched to see which names are slipping through.
# ---------------------------------------------------------------------------
CATEGORIES = {
    "Costume": ["costume", "outfit", "suit", "garb", "uniform", "robes?", "dress", "kimono", "tuxedo"],
    "Head/Armor": ["helm(et)?", "hat", "cap", "hood", "mask", "visor", "crown", "cowl", "armor",
                   "armour", "plate", "mail", "shield", "tiara", "circlet", "hairstyle", "bandana"],
    "Accessory": ["wings?", "cape", "cloak", "tail", "halo", "aura", "ears?", "horns?", "goggles",
                  "glasses", "scarf", "pack", "backpack", "bow", "ribbon", "pin", "bandolier", "trail"],
}
PATTERNS = {
    cat: re.compile(r"\b(?:" + "|".join(words) + r")\b", re.IGNORECASE)
    for cat, words in CATEGORIES.items()
}


def categorize(name: str):
    for cat, pat in PATTERNS.items():
        if pat.search(name):
            return cat
    return None


def key_of(listing: dict) -> str:
    # No listing ID is exposed, and bid changes as people bid, so identify a
    # listing by name + buyout price.
    return f"{listing['name']}|{listing['buyout_price']}"


def load_seen() -> Counter:
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return Counter(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return Counter()


def save_seen(counter: Counter):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(counter, f)


def format_listing(l: dict) -> str:
    buyout = f"{l['buyout_price']:,}" if l["buyout_price"] else "none"
    return f"[{l['category']}] {l['name']}  |  bid {l['bid_price']:,}  |  buyout {buyout}  |  left {l['raw_time'] or '?'}"


def send_alerts(new_items: list):
    body = "\n".join(format_listing(l) for l in new_items)
    print("\n" + "=" * 70)
    print(f"  {len(new_items)} NEW LISTING(S)")
    print("=" * 70)
    print(body)
    print("=" * 70 + "\n")

    try:  # optional desktop notification
        from plyer import notification
        notification.notify(
            title=f"Spiral Knights AH: {len(new_items)} new listing(s)",
            message=body[:250],
            timeout=10,
        )
    except Exception:
        pass

    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if webhook:
        try:
            payload = json.dumps({"content": f"**{len(new_items)} new listing(s)**\n```\n{body[:1800]}\n```"})
            req = urllib.request.Request(
                webhook, data=payload.encode(), headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            log.warning(f"Discord webhook failed: {e}")


async def check_once(evaluator: AuctionEvaluator, args) -> None:
    listings = await evaluator.evaluate_all_auctions()
    if not listings:
        log.info("No listings scraped this round (site slow or layout changed?).")
        return

    tracked = []
    unmatched = set()
    for l in listings:
        cat = categorize(l["name"])
        if cat:
            tracked.append({**l, "category": cat})
        else:
            unmatched.add(l["name"])

    if args.show_unmatched:
        print("Names matching no category:")
        for n in sorted(unmatched):
            print("  ", n)

    # Count-based diff so duplicate listings of the same item are handled
    current = Counter(key_of(l) for l in tracked)
    previous = load_seen()
    first_run = not os.path.exists(SEEN_FILE)

    new_items, budget = [], Counter()
    for k, c in current.items():
        budget[k] = max(0, c - previous.get(k, 0))
    for l in tracked:
        k = key_of(l)
        if budget[k] > 0:
            budget[k] -= 1
            new_items.append(l)

    # Optional: only alert on freshly posted auctions (long time left)
    if args.min_minutes_left:
        new_items = [l for l in new_items if l["time_left"] >= args.min_minutes_left]

    save_seen(current)

    if first_run and not args.alert_first_run:
        log.info(f"First run: recorded {len(tracked)} existing listings as baseline. "
                 f"You'll be alerted about anything new from now on.")
    elif new_items:
        send_alerts(new_items)
    else:
        log.info(f"Checked {len(listings)} listings ({len(tracked)} tracked). Nothing new.")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=300, help="seconds between checks (default 300)")
    ap.add_argument("--once", action="store_true", help="check once and exit")
    ap.add_argument("--alert-first-run", action="store_true", help="alert on everything the first time")
    ap.add_argument("--min-minutes-left", type=int, default=0,
                    help="only alert if the listing has at least this many minutes left (proxy for 'just posted')")
    ap.add_argument("--show-unmatched", action="store_true", help="print names that fit no category")
    args = ap.parse_args()

    evaluator = AuctionEvaluator()
    while True:
        try:
            await check_once(evaluator, args)
        except Exception as e:
            log.error(f"Check failed: {e}")
        if args.once:
            break
        await asyncio.sleep(args.interval)


if __name__ == "__main__":
    asyncio.run(main())
