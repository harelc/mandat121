#!/usr/bin/env python3
"""Committee protocol search — full-corpus backfill + incremental index.

The committee search index (data/search_c/) historically covered only
the 268 protocols that build_protocols.py pre-parsed for the recent
window — ~2.5% of the 10,855 K25 committee sessions. This builder
closes the gap the same way build_plenum_search.py does for the plenum:
enumerate ALL KNS_CommitteeSession rows, stream each protocol docx
(GroupTypeID 23) into word counters only (parsed turns are never
stored — the reader parses on demand via committee-protocol.mjs),
and merge the counters into the existing sharded inverted index.

Safety / incrementality:
  * data/search_c/manifest.json lists indexed session ids; reruns skip
    them. Progress is checkpointed every CHECKPOINT sessions: shards
    are flushed first, then manifest + protocol_search.json — so a
    killed run resumes where it stopped (worst case, the last partial
    batch is re-counted; encode order keeps postings valid regardless).
  * Nothing is deleted: existing 268 sittings' postings stay as-is.
  * No df-pruning is applied here. Pruning is a one-way door only if
    applied at ingest; from full shards it can be applied (and
    re-applied at another threshold) offline at any time.
  * --limit N indexes only the first N todo sessions (smoke tests).

Output (per Knesset; session ids are globally unique, eras never mix
shards): K25: data/search_c/ + data/protocol_search.json;
K26: data/search_c26/ + data/k26_protocol_search.json
({sid: {committee, date, kn}} merged).
The K25 shards are uploaded to R2 by upload_search_shards.py; the client
reads R2 first and falls back to the committed shards. K26 shards are
committed-only until the R2 manifest learns the c26 corpus.
"""
import json
import sys
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from build_protocols import docx_text, parse_text
from build_protocols_search import (SEARCH_C_DIR, add_counts_to_shards,
                                    flush_shards, words_of)

BASE = "https://knesset.gov.il/OdataV4/ParliamentInfo/"
HDR = {"User-Agent": "Mozilla/5.0 mandat121-build", "Accept": "application/json"}
CHECKPOINT = 100


def paths_for(kn):
    """(metadata file, shard dir, committees file) for a Knesset."""
    if kn == 25:
        return (Path("data/protocol_search.json"), SEARCH_C_DIR,
                Path("data/committees.json"))
    return (Path(f"data/k{kn}_protocol_search.json"), Path(f"data/search_c{kn}"),
            Path(f"data/k{kn}_committees.json"))


KNESSET = 25
OUT, SHARD_DIR, COMMITTEES_F = paths_for(KNESSET)
MANIFEST = SHARD_DIR / "manifest.json"


def get(path):
    for attempt in range(3):
        try:
            req = urllib.request.Request(BASE + path, headers=HDR)
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def paged(entity, params):
    """OData caps pages at 100 rows regardless of $top."""
    rows, skip = [], 0
    while True:
        page = get(f"{entity}?{params}&$top=100&$skip={skip}")["value"]
        rows.extend(page)
        if len(page) < 100:
            return rows
        skip += 100


def committee_names(sessions):
    names = {}
    try:
        data = json.loads(COMMITTEES_F.read_text(encoding="utf-8"))
        names = {str(c["id"]): c.get("name", "") for c in data.get("committees", [])}
    except Exception:
        pass
    missing = {s["CommitteeID"] for s in sessions} - {int(k) for k in names}
    if missing:  # committees outside the shipped list — resolve from OData
        for c in paged("KNS_Committee", "$select=Id,Name"):
            if c["Id"] in missing:
                names[str(c["Id"])] = c.get("Name", "")
    return names


def session_counts(sid, urls, committee, date):
    texts = [docx_text(u) for u in urls]
    doc = parse_text({"id": sid, "committee": committee, "start": date},
                     "\n".join(texts))
    counts = Counter()
    for t in doc["turns"]:
        if t["role"] in ("speaker", "chair", "guest"):
            counts.update(words_of(t["text"]))
    return counts


def main():
    global KNESSET, OUT, SHARD_DIR, MANIFEST, COMMITTEES_F
    if "--knesset" not in sys.argv:
        raise SystemExit(f"usage: {sys.argv[0]} --knesset {{25,26}} — the era is required, never defaulted")
    if "--knesset" in sys.argv:
        KNESSET = int(sys.argv[sys.argv.index("--knesset") + 1])
        if KNESSET not in (25, 26):
            raise SystemExit("--knesset must be 25 or 26")
        OUT, SHARD_DIR, COMMITTEES_F = paths_for(KNESSET)
        MANIFEST = SHARD_DIR / "manifest.json"
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    search = {}
    if OUT.exists():
        search = json.loads(OUT.read_text(encoding="utf-8"))
    done_sids = set()
    if MANIFEST.exists():
        done_sids = set(json.loads(MANIFEST.read_text(encoding="utf-8")).get("sessions", []))
    # The 268 pre-parsed sittings predate the manifest — adopt them.
    done_sids |= set(search.keys())
    sessions = paged(
        "KNS_CommitteeSession",
        f"$filter=KnessetNum%20eq%20{KNESSET}&$orderby=StartDate"
        "&$select=Id,CommitteeID,StartDate")
    print(f"{len(sessions)} K{KNESSET} committee sessions", flush=True)
    names = committee_names(sessions)
    todo = [s for s in sessions if str(s["Id"]) not in done_sids]
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} to index ({len(done_sids)} already indexed)", flush=True)
    if not todo:
        print("nothing to do")
        return

    docs = {}
    ids = [s["Id"] for s in todo]
    for i in range(0, len(ids), 25):
        batch = ",".join(str(x) for x in ids[i:i + 25])
        for d in paged("KNS_DocumentCommitteeSession",
                       f"$filter=CommitteeSessionID%20in%20({batch})"
                       "%20and%20GroupTypeID%20eq%2023"
                       "&$select=CommitteeSessionID,FilePath"):
            docs.setdefault(d["CommitteeSessionID"], []).append(d["FilePath"])
    jobs = [(s, docs[s["Id"]]) for s in todo if docs.get(s["Id"])]
    print(f"{len(jobs)} sessions with protocol documents "
          f"({len(todo) - len(jobs)} without)", flush=True)

    def work(job):
        s, urls = job
        committee = names.get(str(s["CommitteeID"]), "")
        date = (s.get("StartDate") or "")[:10]
        counts = session_counts(s["Id"], urls, committee, date)
        return s, counts, {"committee": committee, "date": date, "kn": KNESSET}

    def checkpoint(cache, dirty):
        flush_shards(SHARD_DIR, cache, dirty)
        OUT.write_text(json.dumps(search, ensure_ascii=False), encoding="utf-8")
        SHARD_DIR.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(
            {"sessions": sorted(done_sids, key=int)}, ensure_ascii=False),
            encoding="utf-8")

    cache, dirty = {}, set()
    done = failed = 0
    failed_ids = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(work, j): j[0]["Id"] for j in jobs}
        for fut in as_completed(futs):
            sid = futs[fut]
            try:
                s, counts, entry = fut.result()
                add_counts_to_shards(SHARD_DIR, str(s["Id"]), counts,
                                     cache, dirty)
                search[str(s["Id"])] = entry
                done_sids.add(str(s["Id"]))
            except Exception as e:  # noqa: BLE001
                failed += 1
                failed_ids.append((sid, str(e)[:80]))
                if failed <= 5:  # surface the first failures live — a
                    # 90-min job that only explains itself at the end is
                    # undebuggable (first CI run failed ~100% silently)
                    print(f"FAIL sid={sid}: {type(e).__name__}: {str(e)[:160]}", flush=True)
            done += 1
            if done % CHECKPOINT == 0:
                checkpoint(cache, dirty)
                print(f"{done}/{len(jobs)} done, {failed} failed", flush=True)
    checkpoint(cache, dirty)
    if failed:
        print(f"FAILED ({failed}): {failed_ids[:8]}", flush=True)
    print(f"DONE {len(search)} committee protocols indexed -> {OUT} (+{SHARD_DIR})")


if __name__ == "__main__":
    main()
