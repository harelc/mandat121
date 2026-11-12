#!/usr/bin/env python3
"""Full-term K25 committee-protocol highlights — residue only, no texts.

Why this exists: the in-app committee protocols (data/protocols/*.json,
build_protocols.py) cover only the recent-activity window
(build_committees.RECENT_SINCE — the last ~4 months, 268 sittings),
while K25 committees sat 10,855 times and ~93% of sampled historical
sittings have a protocol document in the official OData. Parsing all
of them into the repo the committee way would ship ~1.7GB of JSON, so
this builder keeps only what MK highlights need, per MK:

  data/cmt_hl.json:
    {_meta, sessions: {sid: [committee, date, doc_url]},
     by_mk: {surname-first: {apps: [[sid, turns, words]…],
                             top: [{sid, turn, role, words, score, excerpt}]}}}

Scoring is the same classic TF-IDF shape as the other corpora
(Σ idf over distinct content terms / √terms; ≥30-word turns), with the
IDF computed over this corpus's candidate turns. It is a third corpus
with its own picks in build_mk_highlights.py — scores are never
compared across corpora. Sessions already parsed into
data/protocols/ are skipped (the full-text corpus owns them; the
merger also dedupes by sid).

Two passes, resumable: pass 1 downloads + parses each protocol once
and writes a per-sitting scratch file to data/cmt_hl_work/ (gitignored
— candidate term lists, ~tens of MB, deleted never; it IS the resume
cache). Pass 2 scores from the scratch files, so re-runs after an
interruption only fetch what is missing. This is a one-time K25
backfill (plus manual re-runs if the full-text window ever moves) —
it is deliberately NOT in the daily workflow: ~10k document downloads
do not belong in a daily job, and K25 is a closed era.

Links: the residue keeps each sitting's official fs.knesset.gov.il
protocol URL — archive highlights open that document, since the
in-app reader only exists for full-text sittings.
Stdlib only. Official sources only (OData + fs.knesset.gov.il).
"""
import json
import math
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from build_mk_highlights import mk_universe, resolve, speaker_map
from build_protocols import docx_text, parse_text
from build_protocols_search import words_of

BASE = "https://knesset.gov.il/OdataV4/ParliamentInfo/"
HDR = {"User-Agent": "Mozilla/5.0 mandat121-build", "Accept": "application/json"}
WORK = Path("data/cmt_hl_work")
OUT = Path("data/cmt_hl.json")
PROTO_DIR = Path("data/protocols")
KNESSET = 25
MIN_WORDS, MIN_TERMS, TOP_PER_MK = 30, 25, 8
MARKER_LEAK = re.compile(r"<<\s*[^>]*>>\s*[^:]{0,40}:")


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


def excerpt_of(text):
    raw = MARKER_LEAK.sub(" — ", " ".join((text or "").split()))
    raw = re.sub(r"<<[^>]*>>", "", raw)
    return " ".join(raw.split())[:300]


def pass1_one(job, by_display, by_pair):
    """Download+parse one sitting -> scratch dict (MK speakers only)."""
    sess, url = job
    doc = parse_text(sess, docx_text(url))
    apps, cand = {}, {}
    for ti, t in enumerate(doc["turns"]):
        if t.get("role") not in ("speaker", "chair"):
            continue
        key = resolve(t.get("speaker", ""), by_display, by_pair)
        if not key:
            continue
        words = len((t.get("text") or "").split())
        a = apps.setdefault(key, [0, 0])
        a[0] += 1
        a[1] += words
        if words >= MIN_WORDS:
            content = words_of(t.get("text", ""))
            if len(content) >= MIN_TERMS:
                cand.setdefault(key, []).append(
                    [ti, t["role"], words, excerpt_of(t.get("text", "")),
                     " ".join(sorted(set(content)))])
    return {"id": str(sess["id"]), "committee": sess.get("committee", ""),
            "date": (sess.get("start") or "")[:10], "doc": url,
            "apps": apps, "cand": cand}


def main():
    force = "--force" in sys.argv
    by_display, by_pair = speaker_map(mk_universe())
    cmap = {}
    try:
        cdata = json.loads(Path("data/committees.json").read_text("utf-8"))
        cmap = {str(c["id"]): c.get("name", "") for c in cdata.get("committees", [])}
    except Exception:
        pass

    sessions = paged("KNS_CommitteeSession",
                     f"$filter=KnessetNum%20eq%20{KNESSET}"
                     "&$select=Id,CommitteeID,StartDate&$orderby=StartDate")
    print(f"{len(sessions)} K{KNESSET} committee sessions", flush=True)
    have_full = {f.stem for f in PROTO_DIR.glob("*.json")}
    WORK.mkdir(parents=True, exist_ok=True)
    have_scratch = {f.stem for f in WORK.glob("*.json")}
    todo_sessions = [s for s in sessions
                     if str(s["Id"]) not in have_full
                     and (force or str(s["Id"]) not in have_scratch)]
    print(f"{len(todo_sessions)} to fetch "
          f"({len(have_full)} full-text, {len(have_scratch)} scratch cached)", flush=True)

    if todo_sessions:
        docs = {}
        ids = [s["Id"] for s in todo_sessions]
        for i in range(0, len(ids), 25):
            batch = ",".join(str(x) for x in ids[i:i + 25])
            for d in paged("KNS_DocumentCommitteeSession",
                           f"$filter=CommitteeSessionID%20in%20({batch})"
                           "&$select=CommitteeSessionID,GroupTypeDesc,FilePath"):
                if "פרוטוקול" in (d.get("GroupTypeDesc") or ""):
                    docs.setdefault(str(d["CommitteeSessionID"]), d["FilePath"])
        jobs = []
        for s in todo_sessions:
            url = docs.get(str(s["Id"]))
            if url:
                jobs.append(({"id": s["Id"], "committee": cmap.get(str(s.get("CommitteeID")), ""),
                              "start": s.get("StartDate") or ""}, url))
        print(f"{len(jobs)} sittings with protocol documents", flush=True)
        done, failed = 0, []
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(pass1_one, j, by_display, by_pair): j[0]["id"] for j in jobs}
            for fut in as_completed(futs):
                sid = futs[fut]
                try:
                    out = fut.result()
                    (WORK / f"{sid}.json").write_text(
                        json.dumps(out, ensure_ascii=False, separators=(",", ":")), "utf-8")
                except Exception as e:
                    failed.append((sid, str(e)[:80]))
                done += 1
                if done % 500 == 0:
                    print(f"{done}/{len(jobs)} parsed, {len(failed)} failed", flush=True)
        if failed:
            print(f"FAILED ({len(failed)}): {failed[:8]}", flush=True)

    # Pass 2: IDF over every candidate turn in the scratch corpus.
    scratch = sorted(WORK.glob("*.json"))
    print(f"scoring from {len(scratch)} scratch sittings", flush=True)
    df, n_cand = Counter(), 0
    parsed = []
    for f in scratch:
        d = json.loads(f.read_text("utf-8"))
        parsed.append(d)
        for lst in d["cand"].values():
            for rec in lst:
                n_cand += 1
                for term in set(rec[4].split()):
                    df[term] += 1
    idf = {t: math.log((n_cand + 1) / (c + 1)) + 1 for t, c in df.items()}
    sessions_out, by_mk = {}, defaultdict(lambda: {"apps": [], "top": []})
    for d in parsed:
        sid = d["id"]
        sessions_out[sid] = [d["committee"], d["date"], d["doc"]]
        for key, (turns, words) in d["apps"].items():
            by_mk[key]["apps"].append([sid, turns, words])
        for key, lst in d["cand"].items():
            for turn, role, words, excerpt, terms in lst:
                terms = terms.split()
                score = sum(idf[t] for t in set(terms)) / math.sqrt(len(terms))
                by_mk[key]["top"].append({"sid": sid, "turn": turn, "role": role,
                                          "words": words, "score": round(score, 2),
                                          "excerpt": excerpt})
    for e in by_mk.values():
        e["apps"].sort(key=lambda a: -a[2])
        e["top"].sort(key=lambda r: -r["score"])
        e["top"] = e["top"][:TOP_PER_MK]
    payload = {"_meta": {
                   "method": "TF-IDF over candidate turns of the full-term "
                             "committee archive (IDF over candidate turns; "
                             "third corpus, picked separately). No LLM.",
                   "sessions": len(sessions_out), "candidate_turns": n_cand,
                   "mks": len(by_mk), "knesset": KNESSET,
                   "note": "Residue only: speaker stats + top turns per MK. "
                           "Full texts remain the recent-window "
                           "data/protocols/ corpus; archive links open the "
                           "official fs.knesset.gov.il document."},
               "sessions": sessions_out, "by_mk": dict(by_mk)}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")
    print(f"DONE {OUT} — {len(sessions_out)} sittings, {len(by_mk)} MKs, "
          f"{sum(len(e['top']) for e in by_mk.values())} highlights")


if __name__ == "__main__":
    main()
