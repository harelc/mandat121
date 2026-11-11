#!/usr/bin/env python3
"""Full per-MK results for every plenum vote of a Knesset era.

Usage: python3 build_k25_vote_results.py [--knesset 25|26]  (default 25;
run build_knesset.py --knesset N first — vote IDs come from k{N}_votes.json.
Missing/empty vote file = exit 0, no write: safe no-op before OData
publishes that Knesset's votes.)

Why: the legacy biapia pipeline only kept per-MK results for preliminary
and third-reading votes (9 second-reading records!), so whole vote types
looked missing. KNS_PlenumVoteResult holds all of it (~526K era rows);
the service caps pages at 100 rows and has no aggregation, so this pools
8 workers, streams pages, and writes:

  data/k{N}_vote_results.json — compact: {"names":[...], "votes":{"<id>":[[nameIdx,1|2|3],...]}}
  data/k{N}_votes.json        — patched in place with for/against/abstain tallies

ResultDesc mapping: בעד=1, נגד=2, נמנע=3 (other descs are non-votes, skipped).
Names are surname-first exactly as bills.json mk_votes (LastName + FirstName).
"""
import argparse, json, time, urllib.parse, urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = "https://knesset.gov.il/OdataV4/ParliamentInfo"
HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 mandat121-build"}
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

def compute_tallies(per_vote):
    """Tallies derived from per-vote rows — the single source of truth.

    Never accumulate tallies incrementally across a resume: the 2026-11-10
    run computed tallies from the checkpoint, then deleted the possibly-
    truncated 100-row votes and refetched them, adding their rows to the
    tallies a second time. 49 votes shipped with exactly doubled counts
    (e.g. 184 against out of 120 MKs) until this was made a pure function
    of the final rows."""
    tallies = {}
    for vid, rows in per_vote.items():
        t = tallies.setdefault(vid, {"for": 0, "against": 0, "abstain": 0})
        for _, code in rows:
            if code in (1, 2, 3):
                t[["for", "against", "abstain"][code - 1]] += 1
    return tallies


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--knesset", type=int, default=25, choices=(25, 26))
    kn = ap.parse_args().knesset
    votes_path = Path(f"data/k{kn}_votes.json")
    if not votes_path.exists():
        print(f"K{kn}: {votes_path} not built yet — skipping vote-results harvest")
        return
    votes = json.load(open(votes_path, encoding="utf-8"))
    if not votes:
        print(f"K{kn}: no plenum votes published yet — skipping vote-results harvest")
        return
    want = {v["id"] for v in votes}
    names, name_idx = [], {}
    per_vote = {}
    tallies = {}
    # Resume from checkpoint if a previous run was interrupted (the service
    # occasionally returns HTTP 481 mid-run; one bad vote must not kill it).
    ckpt = Path(f"data/k{kn}_vote_results.json")
    if ckpt.exists():
        try:
            prev = json.loads(ckpt.read_text(encoding="utf-8"))
            names = prev.get("names", [])
            name_idx = {n: i for i, n in enumerate(names)}
            per_vote = {int(k): v for k, v in prev.get("votes", {}).items()}
            print(f"resumed: {len(per_vote)} votes already harvested", flush=True)
            # Checkpointed votes are trusted as complete: fetches are
            # per-vote (a vote only lands here whole), fetch_vote pages
            # correctly at the service's 100-row cap, and every stored
            # vote has been through it. The old exactly-100-row refetch
            # guarded against the legacy harvester's silent truncation;
            # that data is long replaced, and the closed era's votes do
            # not change upstream. An interrupted run simply leaves votes
            # missing, and `todo` below picks exactly those up.
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
            done += 1
            if done % 500 == 0:
                print(f"{done}/{len(todo)} votes, {rows_seen} rows, {time.time()-t0:.0f}s", flush=True)
            if done % 1000 == 0:  # checkpoint
                ckpt.write_text(json.dumps({"names": names, "votes": {str(k): v for k, v in sorted(per_vote.items())}}, ensure_ascii=False), encoding="utf-8")
    # Sorted vote keys: refetched/resumed votes used to land at the end
    # of the dict, so the file differed byte-wise on runs with zero
    # semantic change — polluting the daily data commit and needlessly
    # retriggering the analytics rebuild that gates on this file.
    out = {"names": names, "votes": {str(k): v for k, v in sorted(per_vote.items())}}
    Path(f"data/k{kn}_vote_results.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    tallies = compute_tallies(per_vote)
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
