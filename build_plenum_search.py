#!/usr/bin/env python3
"""Plenum protocol (דברי הכנסת) search index — search-only, no reader.

Owner scope call (2026-11-10): index ALL Knesset-25 plenum sessions for
word/prefix search like the committee protocols, but do NOT pre-parse/
ship full transcripts — 418 sessions × ~0.5MB of docx would add 100MB+
of parsed JSON to the repo. Full transcripts are parsed on demand by
netlify/functions/plenum.mjs instead (fs.knesset has no CORS headers).

Source: KNS_PlenumSession + KNS_DocumentPlenumSession (GroupTypeID=28,
דברי הכנסת) from the Knesset OData v4 service; documents are '.doc'
files that are really docx, same as committee protocols.

Output:
  data/plenum_search.json — {session_id: {number, date, doc, words}}
  words = top-N content-word counts (shared tokenizer/stopwords with
  build_protocols_search.py), TOC/speaker-header lines excluded.

Incremental: sessions already in the output are skipped (transcripts of
past sessions are immutable), so the daily workflow only downloads new
sessions. Pass --force to rebuild from scratch.
"""
import json
import sys
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from build_protocols import docx_text
from build_protocols_search import TOP_N, words_of

BASE = "https://knesset.gov.il/OdataV4/ParliamentInfo/"
HDR = {"User-Agent": "Mozilla/5.0 mandat121-build", "Accept": "application/json"}
OUT = Path("data/plenum_search.json")
KNESSET = 25


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
    """OData service caps pages at 100 rows regardless of $top."""
    rows, skip = [], 0
    while True:
        page = get(f"{entity}?{params}&$top=100&$skip={skip}")["value"]
        rows.extend(page)
        if len(page) < 100:
            return rows
        skip += 100


def session_words(url):
    text = docx_text(url)
    kept = []
    for para in text.split("\n"):
        p = para.strip()
        if not p or "PAGEREF" in p or p.startswith("תוכן עניינים"):
            continue  # TOC block: page refs + repeated speaker names
        if p.endswith(":") and len(p) < 80:
            continue  # speaker header line ("name (faction):"), not speech
        kept.append(p)
    return Counter(words_of(" ".join(kept)))


def main():
    force = "--force" in sys.argv
    index = {}
    if OUT.exists() and not force:
        index = json.loads(OUT.read_text(encoding="utf-8"))
    sessions = paged(
        "KNS_PlenumSession",
        f"$filter=KnessetNum%20eq%20{KNESSET}&$orderby=StartDate"
        "&$select=Id,Number,StartDate")
    print(f"{len(sessions)} K{KNESSET} plenum sessions", flush=True)

    todo = [s for s in sessions if str(s["Id"]) not in index]
    print(f"{len(todo)} new to index ({len(index)} cached)", flush=True)
    if todo:
        docs = {}  # session id -> [docx urls]
        ids = [s["Id"] for s in todo]
        for i in range(0, len(ids), 25):
            batch = ",".join(str(x) for x in ids[i:i + 25])
            for d in paged("KNS_DocumentPlenumSession",
                           f"$filter=PlenumSessionID%20in%20({batch})"
                           "&$select=PlenumSessionID,GroupTypeID,FilePath"):
                if d["GroupTypeID"] == 28:  # דברי הכנסת only
                    docs.setdefault(d["PlenumSessionID"], []).append(d["FilePath"])
        jobs = [(s, docs[s["Id"]]) for s in todo if docs.get(s["Id"])]
        print(f"{len(jobs)} sessions with דברי הכנסת documents", flush=True)

        def work(job):
            s, urls = job
            counts = Counter()
            for u in urls:  # a session may have several parts — merge
                counts.update(session_words(u))
            return s, {"number": s["Number"],
                       "date": (s.get("StartDate") or "")[:10],
                       "doc": urls[0],
                       "words": dict(counts.most_common(TOP_N))}

        done, failed = 0, []
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(work, j): j[0]["Id"] for j in jobs}
            for fut in as_completed(futs):
                sid = futs[fut]
                try:
                    s, entry = fut.result()
                    index[str(s["Id"])] = entry
                except Exception as e:
                    failed.append((sid, str(e)[:80]))
                done += 1
                if done % 40 == 0:
                    print(f"{done}/{len(jobs)} done, {len(failed)} failed", flush=True)
        if failed:
            print(f"FAILED: {failed[:8]}", flush=True)
    OUT.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    print(f"DONE {len(index)} plenum sessions indexed -> {OUT}")


if __name__ == "__main__":
    main()
