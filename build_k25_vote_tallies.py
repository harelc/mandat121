#!/usr/bin/env python3
"""Tally every K25-era plenum vote from KNS_PlenumVoteResult.

The vote header entity carries no totals, and the service has no $apply
aggregation (HTTP 473) and caps pages at 100 rows, so this fetches the
~526K era result rows with a small worker pool, aggregates for/against/
abstain per VoteID, and patches data/k25_votes.json in place.

Per-MK results are NOT stored here (that is the curated bills.json layer);
this only gives every one of the 7,557 votes its real tally.
"""
import json, sys, time, urllib.parse, urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "https://knesset.gov.il/OdataV4/ParliamentInfo"
HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 mandat121-build"}
ERA = "2022-11-15T00:00:00Z"
TOTAL = 526_483  # @odata.count at build time; loop stops on short page anyway
MAP = {"בעד": "for", "נגד": "against", "נמנע": "abstain"}

def fetch_page(skip):
    params = {"$filter": f"VoteDate ge {ERA}", "$select": "VoteID,ResultDesc",
              "$orderby": "Id", "$top": "100", "$skip": str(skip), "$format": "json"}
    url = f"{BASE}/KNS_PlenumVoteResult?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=120) as r:
                return skip, json.loads(r.read().decode()).get("value", [])
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))

def main():
    path = Path("data/k25_votes.json")
    votes = json.load(open(path, encoding="utf-8"))
    want = {v["id"] for v in votes}
    tallies = {}
    skips = list(range(0, TOTAL + 100, 100))
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        for skip, rows in ex.map(fetch_page, skips):
            for r in rows:
                vid = r["VoteID"]
                if vid in want:
                    t = tallies.setdefault(vid, {"for": 0, "against": 0, "abstain": 0})
                    k = MAP.get(r["ResultDesc"])
                    if k:
                        t[k] += 1
            done += 1
            if done % 250 == 0:
                print(f"{done}/{len(skips)} pages, {len(tallies)} votes tallied, {time.time()-t0:.0f}s", flush=True)
            if len(rows) < 100 and skip > TOTAL - 1000:
                pass  # cannot break map; short pages near the end are expected
    patched = 0
    for v in votes:
        t = tallies.get(v["id"])
        if t:
            v.update(t)
            patched += 1
    json.dump(votes, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    kinds = Counter(v["kind"] for v in votes if v.get("for") is not None)
    print(f"DONE patched {patched}/{len(votes)} votes in {time.time()-t0:.0f}s; tallied by kind: {dict(kinds)}")

if __name__ == "__main__":
    main()
