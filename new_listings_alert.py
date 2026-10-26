"""
new_listings_alert.py  (API version, no browser, no extra installs)

Posts NEW Spiral Knights auction listings in costume / head & armor / accessory
categories to a Discord channel, using the public API at api.spiralknights.market.

    python new_listings_alert.py --once          # one check, then exit
    python new_listings_alert.py --interval 300  # check every 5 minutes, forever
    python new_listings_alert.py --once --list-categories
    python new_listings_alert.py --once --categories COSTUME,HAT   # force exact codes

Needs only Python 3.8+ (standard library). Set DISCORD_WEBHOOK_URL to your webhook.
Data: Spiral Knights market data from spiralknights.market (CC BY 4.0).
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.spiralknights.market"
SEEN_FILE = "seen_listings.json"
MAX_SEEN = 20000
UA = "sk-auction-discord-alert/1.0 (personal hobby script)"

# I couldn't see the real category codes, so categories are auto-picked when
# their code contains one of these words. The run log prints every available
# category and which ones were chosen; use --categories to override exactly.
CATEGORY_HINTS = re.compile(
    r"costume|armou?r|helm|hat|head|access|wing|cape|mask|hair|outfit|cosmetic", re.I
)


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}", flush=True)


def http(url, data=None, retries=3):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(20)
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(5)
                continue
            raise


def api_get(path, params=None):
    q = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None}, doseq=True)
    return http(f"{API}{path}" + (f"?{q}" if q else ""))


def load_seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("ids", []), True
    except (FileNotFoundError, json.JSONDecodeError):
        return [], False


def save_seen(ids):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"ids": ids[-MAX_SEEN:]}, f)


def pick_categories(args):
    cats = [c["category"] for c in api_get("/v1/auction/categories").get("categories", [])]
    log(f"Available categories: {', '.join(cats) or '(none)'}")
    if args.categories:
        chosen = [c.strip() for c in args.categories.split(",") if c.strip()]
    else:
        chosen = [c for c in cats if CATEGORY_HINTS.search(c)]
    log(f"Watching categories: {', '.join(chosen) or '(none)'}")
    return chosen


def fetch_unseen(category, seen, max_pages):
    """Walk newest-first; stop once a page contains something we've already seen."""
    found, cursor = [], None
    for _ in range(max_pages):
        data = api_get("/v1/auction/listings",
                       {"category": category, "sort": "newest", "limit": 100, "cursor": cursor})
        rows = data.get("listings", [])
        fresh = [r for r in rows if r["auction_id"] not in seen]
        found += fresh
        cursor = data.get("next_cursor")
        if len(fresh) < len(rows) or not cursor:
            break
        time.sleep(0.5)
    return found


def lookup_names(slugs):
    names = {}
    slugs = sorted(set(slugs))
    for i in range(0, len(slugs), 25):
        chunk = slugs[i:i + 25]
        for it in api_get("/v1/auction/items/lookup", {"slug": chunk}).get("items", []):
            names[it["slug"]] = it["name"]
    return names


def fmt(row, category, names):
    name = names.get(row["slug"], row["slug"].replace("_", " "))
    variant = f" ({row['variant']})" if row.get("variant") else ""
    count = f" x{row['count']}" if row.get("count") and row["count"] > 1 else ""
    bid = f"{row['bid_price']:,}" if row.get("bid_price") is not None else "?"
    buy = f"{row['buy_price']:,}" if row.get("buy_price") else "none"
    left = (row.get("time_left") or "?").replace("_", " ").title()
    return f"[{category}] **{name}**{variant}{count}  |  bid {bid}  |  buyout {buy}  |  {left}"


def send_discord(lines):
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    for line in lines:
        print(line)
    if not webhook:
        log("DISCORD_WEBHOOK_URL not set; printed to console only.")
        return
    header = f"**{len(lines)} new listing(s)**"
    footer = "\n-# Spiral Knights market data from spiralknights.market"
    chunk, chunks = header, []
    for line in lines:
        if len(chunk) + len(line) + 1 > 1800:
            chunks.append(chunk)
            chunk = ""
        chunk += "\n" + line
    chunks.append(chunk)
    chunks[-1] += footer
    for c in chunks:
        try:
            http(webhook, data=json.dumps({"content": c}).encode())
        except Exception as e:
            log(f"Discord post failed: {e}")
        time.sleep(1)


def check_once(args):
    chosen = pick_categories(args)
    if args.list_categories:
        return True
    if not chosen:
        log("No categories matched. Re-run with --categories CODE1,CODE2 using the list above.")
        return False

    seen_list, had_file = load_seen()
    seen = set(seen_list)
    baseline = not had_file
    max_pages = 40 if baseline else 5

    new_rows = []
    for cat in chosen:
        for row in fetch_unseen(cat, seen, max_pages):
            new_rows.append((cat, row))
        time.sleep(0.5)

    # Newest first from the API; keep ids in oldest -> newest order for pruning
    seen_list += [r["auction_id"] for _, r in reversed(new_rows)]
    save_seen(seen_list)

    if baseline and not args.alert_first_run:
        log(f"First run: saved {len(new_rows)} existing listings as baseline. Alerts start next check.")
        return True
    if not new_rows:
        log("Nothing new.")
        return True

    names = lookup_names([r["slug"] for _, r in new_rows])
    send_discord([fmt(r, c, names) for c, r in new_rows])
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="check once and exit")
    ap.add_argument("--interval", type=int, default=300, help="seconds between checks")
    ap.add_argument("--categories", help="comma-separated exact category codes to watch")
    ap.add_argument("--list-categories", action="store_true", help="print categories and exit")
    ap.add_argument("--alert-first-run", action="store_true", help="alert on everything the first time")
    args = ap.parse_args()

    while True:
        try:
            ok = check_once(args)
        except Exception as e:
            log(f"Check failed: {e}")
            ok = False
        if args.once or args.list_categories:
            sys.exit(0 if ok else 1)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
