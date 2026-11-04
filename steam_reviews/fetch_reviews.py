#!/usr/bin/env python3
"""Download ALL Steam reviews of a game into a table (CSV + XLSX), incrementally.

Usage:
    python3 fetch_reviews.py                       # finds "Quarantine Zone: The Last Check"
    python3 fetch_reviews.py --appid 1234567       # explicit app id
    python3 fetch_reviews.py --full                # ignore saved state, re-download everything

Files written to --out-dir (default: ./data):
    reviews.csv    - every review, one row per review (deduplicated by recommendationid)
    reviews.xlsx   - same table for Excel
    state.json     - app id, newest review timestamp seen, time of last run

Incremental logic: reviews are requested with filter=recent (newest first, by
creation time). On a repeated run we stop as soon as we reach a review whose
`timestamp_created` is <= the newest timestamp saved in state.json, so only new
reviews are downloaded. Edited reviews are refreshed with an extra pass over
filter=updated down to the last run time.
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

GAME_NAME = "Quarantine Zone: The Last Check"
UA = "steam-reviews-export/1.0"

FIELDS = [
    "recommendationid", "created_utc", "updated_utc", "timestamp_created", "timestamp_updated",
    "language", "voted_up", "votes_up", "votes_funny", "weighted_vote_score", "comment_count",
    "steam_purchase", "received_for_free", "written_during_early_access",
    "playtime_at_review_h", "playtime_forever_h", "author_steamid", "author_num_reviews",
    "developer_response", "review", "fetched_at_utc",
]


def get_json(url, params, retries=6):
    full = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.geturl().split("?")[0] != url:
                    sys.exit(f"Steam redirected {url} -> {r.geturl()}: it refuses this network. "
                             "Run the script from a regular home/office connection.")
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # network hiccup / 429
            wait = 2 ** attempt
            print(f"  request failed ({e}); retry in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"giving up on {full}")


def find_appid(name):
    data = get_json("https://store.steampowered.com/api/storesearch/",
                    {"term": name, "l": "english", "cc": "US"})
    items = data.get("items", [])
    if not items:
        sys.exit(f"Game '{name}' not found in Steam search; pass --appid")
    norm = lambda s: "".join(c for c in s.lower() if c.isalnum())
    for it in items:
        if norm(it["name"]) == norm(name):
            return it["id"], it["name"]
    print("Exact match not found, candidates:", [(i["id"], i["name"]) for i in items], file=sys.stderr)
    return items[0]["id"], items[0]["name"]


def iso(ts):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def to_row(r, fetched_at):
    a = r.get("author", {})
    return {
        "recommendationid": r["recommendationid"],
        "created_utc": iso(r["timestamp_created"]),
        "updated_utc": iso(r["timestamp_updated"]),
        "timestamp_created": r["timestamp_created"],
        "timestamp_updated": r["timestamp_updated"],
        "language": r.get("language", ""),
        "voted_up": r.get("voted_up"),
        "votes_up": r.get("votes_up", 0),
        "votes_funny": r.get("votes_funny", 0),
        "weighted_vote_score": r.get("weighted_vote_score", ""),
        "comment_count": r.get("comment_count", 0),
        "steam_purchase": r.get("steam_purchase"),
        "received_for_free": r.get("received_for_free"),
        "written_during_early_access": r.get("written_during_early_access"),
        "playtime_at_review_h": round(a.get("playtime_at_review", 0) / 60, 1),
        "playtime_forever_h": round(a.get("playtime_forever", 0) / 60, 1),
        "author_steamid": a.get("steamid", ""),
        "author_num_reviews": a.get("num_reviews", ""),
        "developer_response": r.get("developer_response", ""),
        "review": r.get("review", ""),
        "fetched_at_utc": fetched_at,
    }


def crawl(appid, filter_, stop_ts, ts_key, fetched_at):
    """Yield reviews newest-first until a review older than stop_ts appears."""
    cursor, seen_cursors, page = "*", set(), 0
    while True:
        data = get_json(f"https://store.steampowered.com/appreviews/{appid}", {
            "json": 1, "filter": filter_, "language": "all", "review_type": "all",
            "purchase_type": "all", "num_per_page": 100, "cursor": cursor,
            "filter_offtopic_activity": 0,
        })
        if page == 0 and filter_ == "recent":
            print("Steam summary:", data.get("query_summary"))
        reviews = data.get("reviews", [])
        page += 1
        if not reviews:
            return
        for r in reviews:
            if stop_ts and int(r[ts_key]) <= stop_ts:
                return
            yield to_row(r, fetched_at)
        print(f"  [{filter_}] page {page}: +{len(reviews)}")
        cursor = data.get("cursor")
        if not cursor or cursor in seen_cursors:
            return
        seen_cursors.add(cursor)
        time.sleep(0.7)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--appid", type=int)
    ap.add_argument("--name", default=GAME_NAME)
    ap.add_argument("--out-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    ap.add_argument("--full", action="store_true", help="re-download everything")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    csv_path = os.path.join(args.out_dir, "reviews.csv")
    state_path = os.path.join(args.out_dir, "state.json")

    state = {}
    if os.path.exists(state_path) and not args.full:
        state = json.load(open(state_path, encoding="utf-8"))

    appid = args.appid or state.get("appid")
    title = state.get("title", args.name)
    if not appid:
        appid, title = find_appid(args.name)
    print(f"App: {title} ({appid})")

    rows = {}
    if os.path.exists(csv_path) and not args.full:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                rows[r["recommendationid"]] = r

    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    last_created = int(state.get("newest_timestamp_created", 0))
    last_run_ts = int(state.get("last_run_unix", 0))

    new = 0
    for row in crawl(appid, "recent", last_created, "timestamp_created", fetched_at):
        if row["recommendationid"] not in rows:
            new += 1
        rows[row["recommendationid"]] = row
    edited = 0
    if last_run_ts:  # refresh reviews edited since last run
        for row in crawl(appid, "updated", last_run_ts, "timestamp_updated", fetched_at):
            edited += 1
            rows[row["recommendationid"]] = row

    ordered = sorted(rows.values(), key=lambda r: int(r["timestamp_created"]), reverse=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(ordered)

    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "reviews"
        ws.append(FIELDS)
        for r in ordered:
            ws.append([str(r.get(k, "")) if k == "review" else r.get(k, "") for k in FIELDS])
        ws.freeze_panes = "A2"
        wb.save(os.path.join(args.out_dir, "reviews.xlsx"))
    except ImportError:
        print("openpyxl not installed - skipped xlsx")

    newest = max((int(r["timestamp_created"]) for r in ordered), default=0)
    state = {
        "appid": appid, "title": title,
        "newest_timestamp_created": newest,
        "newest_created_utc": iso(newest) if newest else "",
        "last_run_unix": int(time.time()), "last_run_utc": fetched_at,
        "total_reviews_saved": len(ordered),
    }
    json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"Done: {new} new, {edited} edited refreshed, {len(ordered)} total -> {csv_path}")


if __name__ == "__main__":
    main()
