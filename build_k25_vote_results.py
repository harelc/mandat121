#!/usr/bin/env python3
"""Full per-MK results for every K25-era plenum vote.

Why: the legacy biapia pipeline only kept per-MK results for preliminary
and third-reading votes (9 second-reading records!), so whole vote types
looked missing. KNS_PlenumVoteResult holds all of it (~526K era rows);
the service caps pages at 100 rows and has no aggregation, so this pools
8 workers, streams pages, and writes:

  data/k25_vote_results.json — compact: {"names":[...], "votes":{"<id>":[[nameIdx,1|2|3],...]}}
  data/k25_votes.json        — patched in place with for/against/abstain tallies

ResultDesc mapping: בעד=1, נגד=2, נמנע=3 (other descs are non-votes, skipped).
Names are surname-first exactly as bills.json mk_votes (LastName + FirstName).
"""
import json, time, urllib.parse, urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = "https://knesset.gov.il/OdataV4/ParliamentInfo"
HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 mandat121-build"}
ERA = "2022-11-15T00:00:00Z"
TOTAL = 526_483
CODE = {"בעד": 1, "נגד": 2, "נמנע": 3}

def fetch_vote(vid):
    """All result rows for one vote. NOTE: era-wide $skip pagination is
    unusably slow on this service (deep offsets take 30s+/page); per-vote
    queries are ~0.6s, so partition by VoteID instead."""
    rows, skip = [], 0
    while True:
        # The service caps pages at 100 rows regardless of $top (verified:
        # 1,008 harvested votes sat at exactly 100 rows). Page at 100 and
        # keep going while a full page comes back.
        params = {"$filter": f"VoteID eq {vid}", "$select": "VoteID,ResultDesc,LastName,FirstName",
                  "$top": "100", "$skip": str(skip), "$format": "json"}
        url = f"{BASE}/KNS_PlenumVoteResult?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=120) as r:
                    page = json.loads(r.read().decode()).get("value", [])
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))
        rows += page
        if len(page) < 100:
            return vid, rows
        skip += 100

def main():
    votes_path = Path("data/k25_votes.json")
    votes = json.load(open(votes_path, encoding="utf-8"))
    want = {v["id"] for v in votes}
    names, name_idx = [], {}
    per_vote = {}
    tallies = {}
    # Resume from checkpoint if a previous run was interrupted (the service
    # occasionally returns HTTP 481 mid-run; one bad vote must not kill it).
    ckpt = Path("data/k25_vote_results.json")
    if ckpt.exists():
        try:
            prev = json.loads(ckpt.read_text(encoding="utf-8"))
            names = prev.get("names", [])
            name_idx = {n: i for i, n in enumerate(names)}
            per_vote = {int(k): v for k, v in prev.get("votes", {}).items()}
            for vid, rows_ in per_vote.items():
                t = tallies.setdefault(vid, {"for": 0, "against": 0, "abstain": 0})
                for _, code in rows_:
                    t[["for", "against", "abstain"][code - 1]] += 1
            print(f"resumed: {len(per_vote)} votes already harvested", flush=True)
            # Votes at exactly 100 rows may be truncated by the old $top=200
            # bug — refetch them (a genuine 100-voter vote just costs a page).
            truncated = [vid for vid, rows_ in per_vote.items() if len(rows_) == 100]
            for vid in truncated:
                del per_vote[vid]
            if truncated:
                print(f"refetching {len(truncated)} possibly-truncated votes", flush=True)
        except Exception:
            pass
    todo = sorted(want - set(per_vote))
    failed = []
    t0, done, rows_seen = time.time(), 0, 0
    # 10 workers trip the service's throttling (HTTP 481) on a stubborn
    # subset of votes; 3 workers with the retry backoff get through.
    import os
    with ThreadPoolExecutor(max_workers=int(os.environ.get("VOTE_WORKERS", "3"))) as ex:
        futs = [ex.submit(fetch_vote, vid) for vid in todo]
        for fut in as_completed(futs):
            try:
                vid, rows = fut.result()
            except Exception as e:
                failed.append(str(e)[:60])
                continue
            rows_seen += len(rows)
            for r in rows:
                code = CODE.get(r["ResultDesc"])
                if not code:
                    continue
                nm = " ".join(f"{r.get('LastName') or ''} {r.get('FirstName') or ''}".split())
                i = name_idx.get(nm)
                if i is None:
                    i = len(names)
                    name_idx[nm] = i
                    names.append(nm)
                per_vote.setdefault(vid, []).append([i, code])
                t = tallies.setdefault(vid, {"for": 0, "against": 0, "abstain": 0})
                t[["for", "against", "abstain"][code - 1]] += 1
            done += 1
            if done % 500 == 0:
                print(f"{done}/{len(todo)} votes, {rows_seen} rows, {time.time()-t0:.0f}s", flush=True)
            if done % 1000 == 0:  # checkpoint
                ckpt.write_text(json.dumps({"names": names, "votes": {str(k): v for k, v in per_vote.items()}}, ensure_ascii=False), encoding="utf-8")
    out = {"names": names, "votes": {str(k): v for k, v in per_vote.items()}}
    Path("data/k25_vote_results.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    patched = 0
    for v in votes:
        t = tallies.get(v["id"])
        if t:
            v.update(t)
            patched += 1
    json.dump(votes, open(votes_path, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"DONE {rows_seen} rows, {len(names)} MKs, patched {patched}/{len(votes)} tallies, {time.time()-t0:.0f}s")
    print("votes with per-MK results:", len(per_vote), "failed votes this run:", len(failed), failed[:5])

if __name__ == "__main__":
    main()
