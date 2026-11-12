#!/usr/bin/env python3
"""Per-sitting plenum highlight candidates, scored at parse time.

Committee highlights (build_mk_highlights.py) score turns with a
global turn-level IDF because the parsed committee protocols live in
the repo. Plenum transcripts do NOT (418 sittings, ~100MB of docx —
they are parsed on demand by netlify/functions/plenum.mjs), so plenum
turns are scored here, once, when a sitting is parsed:

  idf(t) = ln((S+1)/(df(t)+1)) + 1,  df = sittings containing t —
  read straight from the sharded plenum search index
  (data/search_p/*.json, build_plenum_search.py), S = indexed sittings;
  turn score = Σ idf(distinct content terms) / √(content terms),
  same shape as the committee scorer, on a session-level IDF.

Only the residue is kept, per sitting, in data/plenum_hl/{sid}.json:
speaker turn/word totals (for MK appearances) and each speaker's top
`PER_SPEAKER` candidate turns (excerpt + score). A full candidate
cache with term lists would run to ~33MB; this runs to a few MB, and
the transcripts themselves stay out of the repo. Scores are computed
against the corpus indexed at parse time, so IDF drifts slightly as
new sittings land — rankings within a sitting are exact, across
sittings approximate. The UI therefore never compares committee and
plenum scores directly (separate picks per source).

Two callers:
  * build_plenum_search.py — writes the file for every NEW sitting it
    indexes (daily, incremental; this module's session_hl()).
  * this script as __main__ — one-time backfill over all sittings in
    data/plenum_search.json (skips sittings whose file exists).
Stdlib only.
"""
import json
import math
import re
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from build_protocols import docx_text, parse_text
from build_protocols_search import SEARCH_P_DIR, words_of

try:  # keep only MK-resolved speakers: guests/officials are dropped here,
    # exactly as build_mk_highlights.resolve() would drop them later —
    # storing them cost ~30MB of residue nobody can use.
    from build_mk_highlights import mk_universe, resolve, speaker_map
    _BD, _BP = speaker_map(mk_universe())
    def is_mk_speaker(name):
        return resolve(name, _BD, _BP) is not None
except Exception:  # highlights builder unavailable — keep everyone
    def is_mk_speaker(name):
        return True

HL_DIR = Path("data/plenum_hl")
PLENUM_INDEX = Path("data/plenum_search.json")
BASE = "https://knesset.gov.il/OdataV4/ParliamentInfo/"
HDR = {"User-Agent": "Mozilla/5.0 mandat121-build", "Accept": "application/json"}
MIN_WORDS, MIN_TERMS, PER_SPEAKER = 30, 25, 2
MARKER_LEAK = re.compile(r"<<\s*[^>]*>>\s*[^:]{0,40}:")


def session_df(shard_dir=SEARCH_P_DIR):
    """word -> number of sittings containing it, from the search shards
    (postings are "sid:count,delta:count,…" — count the entries)."""
    df = Counter()
    for f in shard_dir.glob("*.json"):
        if f.name == "manifest.json":
            continue
        for w, enc in json.loads(f.read_text("utf-8")).items():
            df[w] += enc.count(",") + 1 if isinstance(enc, str) else len(enc)
    return df


def idf_map(df, n_sessions):
    return {t: math.log((n_sessions + 1) / (c + 1)) + 1 for t, c in df.items()}


def session_hl(doc, meta, idf, n_sessions):
    """{speakers, top} for one parsed sitting (build_protocols shape).

    `top` keeps each speaker's best PER_SPEAKER substantive turns;
    speakers are given-first here — MK resolution happens in
    build_mk_highlights.py, the one place that knows the MK universe."""
    top = {}
    for ti, t in enumerate(doc.get("turns", [])):
        if t.get("role") not in ("speaker", "chair"):
            continue
        words = len((t.get("text") or "").split())
        if words < MIN_WORDS:
            continue
        content = words_of(t.get("text", ""))
        if len(content) < MIN_TERMS:
            continue
        score = sum(idf.get(w, math.log(n_sessions + 1) + 1)
                    for w in set(content)) / math.sqrt(len(content))
        raw = MARKER_LEAK.sub(" — ", " ".join((t.get("text") or "").split()))
        raw = re.sub(r"<<[^>]*>>", "", raw)  # << הצח >> and friends, inline
        rec = {"turn": ti, "role": t["role"], "words": words,
               "score": round(score, 2),
               "excerpt": " ".join(raw.split())[:300]}
        lst = top.setdefault(t["speaker"], [])
        lst.append(rec)
        lst.sort(key=lambda r: -r["score"])
        del lst[PER_SPEAKER:]
    speakers = {k: v for k, v in doc.get("speakers", {}).items() if is_mk_speaker(k)}
    top = {k: v for k, v in top.items() if is_mk_speaker(k)}
    return {"id": str(doc["id"]), "number": meta.get("number"),
            "date": meta.get("date", doc.get("date", "")),
            "speakers": speakers, "top": top}


def write_hl(doc, meta, idf, n_sessions, hl_dir=HL_DIR):
    hl_dir.mkdir(parents=True, exist_ok=True)
    out = session_hl(doc, meta, idf, n_sessions)
    (hl_dir / f"{doc['id']}.json").write_text(
        json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


# ---- backfill (one-time; daily updates ride build_plenum_search) ----

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
    rows, skip = [], 0
    while True:
        page = get(f"{entity}?{params}&$top=100&$skip={skip}")["value"]
        rows.extend(page)
        if len(page) < 100:
            return rows
        skip += 100


def merge_parts(sid, date, texts):
    turns, speakers = [], {}
    for text in texts:
        doc = parse_text({"id": sid, "committee": "מליאה", "start": date}, text)
        turns.extend(doc["turns"])  # turn indices stay merged-order: session_hl enumerates this list
        for name, s in doc["speakers"].items():
            a = speakers.setdefault(name, {"turns": 0, "words": 0,
                                           "role": s["role"], "faction_hint": s.get("faction_hint", "")})
            a["turns"] += s["turns"]
            a["words"] += s["words"]
    return {"id": sid, "date": date, "turns": turns, "speakers": speakers}


def main():
    import sys
    force = "--force" in sys.argv
    index = json.loads(PLENUM_INDEX.read_text("utf-8"))
    todo = [(sid, m) for sid, m in index.items()
            if force or not (HL_DIR / f"{sid}.json").exists()]
    print(f"{len(todo)} sittings to backfill ({len(index)} indexed)", flush=True)
    if not todo:
        return
    idf = idf_map(session_df(), len(index))
    docs = {}
    ids = [int(s) for s, _ in todo]
    for i in range(0, len(ids), 25):
        batch = ",".join(str(x) for x in ids[i:i + 25])
        for d in paged("KNS_DocumentPlenumSession",
                       f"$filter=PlenumSessionID%20in%20({batch})"
                       "&$select=PlenumSessionID,GroupTypeID,FilePath"):
            if d["GroupTypeID"] == 28:
                docs.setdefault(str(d["PlenumSessionID"]), []).append(d["FilePath"])

    def work(item):
        sid, meta = item
        texts = [docx_text(u) for u in docs[sid]]
        return write_hl(merge_parts(sid, meta["date"], texts), meta, idf, len(index))

    done, failed = 0, []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(work, it): it[0] for it in todo if docs.get(it[0])}
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception as e:
                failed.append((futs[fut], str(e)[:80]))
            done += 1
            if done % 50 == 0:
                print(f"{done}/{len(futs)} done, {len(failed)} failed", flush=True)
    print(f"DONE {done - len(failed)} plenum highlight files -> {HL_DIR}"
          + (f"; FAILED {failed[:5]}" if failed else ""))


if __name__ == "__main__":
    main()
