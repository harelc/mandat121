#!/usr/bin/env python3
"""Per-MK protocol highlights, scored classically — no LLM.

Reads the parsed committee protocols (data/protocols/*.json, built by
build_protocols.py from the official fs.knesset.gov.il transcripts) and
answers, for every MK who speaks in them: where did they speak, how
much, and which of their turns are worth reading first?

Scoring (deliberately old-school information retrieval, fully
reproducible from the transcripts):
  * tokenise with the protocol search index's tokenizer/stopwords
    (build_protocols_search.words_of — one vocabulary for both),
  * IDF over turns: idf(t) = ln((N+1)/(df(t)+1)) + 1, N = all scored
    turns, df = turns containing t — a term said in every sitting
    ("הוועדה", "אדוני") carries almost no signal,
  * a turn's score = Σ idf(t) over its DISTINCT content terms, divided
    by √(content-term count): rare, specific vocabulary raises the
    score, sheer length alone does not (the √ keeps a long turn ahead
    of a short one only when it keeps saying new, specific things),
  * only substantive turns are candidates: ≥30 spoken words and ≥25
    content terms — greetings, thanks and procedural chair turns fall
    out on length before IDF ever sees them,
  * at most 2 highlights per sitting per MK, so the top-5 spans their
    range instead of five turns from one long day.

Output: data/mk_highlights.json
  {_meta: {method, sessions, turns_scored, mks},
   by_mk: {surname-first: {turns, words, sessions,
             appearances: [{sid, committee, date, turns, words}],
             highlights:  [{sid, committee, date, turn, role, score,
                            words, excerpt}]}}}
`turn` is the index into that protocol's turns[] — the UI opens the
session's protocol filtered to the speaker; the excerpt is the turn's
own text (first 300 chars), verbatim from the official transcript.

Speaker→MK matching: protocol speakers are given-first ("שמחה רוטמן"),
our keys surname-first ("רוטמן שמחה"). The map is built by inverting
index.html's disp() (incl. its FORWARD exceptions — keep in sync), with
an unambiguous (surname, first-given-name) token fallback for speakers
the transcript spells with a middle name ("מיכל מרים וולדיגר").
Unmatched speakers (guests, officials) are simply not MKs — skipped.
Stdlib only.
"""
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from build_protocols_search import words_of

PROTO_DIR = Path("data/protocols")
PLENUM_HL_DIR = Path("data/plenum_hl")
OUT = Path("data/mk_highlights.json")
MIN_WORDS, MIN_TERMS, TOP_N, PER_SESSION_CAP = 30, 25, 5, 2
PICKS_PER_SOURCE = 2  # top picks per corpus per MK (committee / plenum / committee archive — scores are per-corpus, never compared across sources)
CMT_HL = Path("data/cmt_hl.json")

# index.html FORWARD: surname-first key -> given-first display, for
# multi-token surnames the naive token move gets wrong. Keep in sync.
FORWARD = {"פרקש הכהן אורית": "אורית פרקש הכהן", "בן גביר איתמר": "איתמר בן גביר",
           "בן ארי מירב": "מירב בן ארי", "בן ברק רם": "רם בן ברק",
           "בן צור יואב": "יואב בן צור", "בן סעיד סמיר": "סמיר בן סעיד",
           "דיסטל אטבריאן גלית": "גלית דיסטל-אטבריאן",
           "ח'טיב יאסין אימאן": "אימאן ח'טיב יאסין", "טור פז משה": "משה טור פז",
           "להב הרצנו יוראי": "יוראי להב הרצנו", "סון הר מלך לימור": "לימור סון הר מלך",
           "צרפתי הרכבי מטי": "מטי צרפתי הרכבי", "רון בן משה יעל": "יעל רון בן משה",
           "רייטן מרום אפרת": "אפרת רייטן מרום", "שאשא ביטון יפעת": "יפעת שאשא-ביטון",
           "שיר סגמן מיכל": "מיכל שיר סגמן", "תומא סלימאן עאידה": "עאידה תומא סלימאן"}


def disp(key):
    if key in FORWARD:
        return FORWARD[key]
    p = key.split()
    # Particle surnames (בן צור, אבו רוקן, אל טורי): the surname is the
    # first two tokens, not just the first.
    if len(p) >= 3 and p[0] in ("בן", "אבו", "אל"):
        return " ".join(p[2:] + p[:2])
    return " ".join(p[1:] + p[:1]) if len(p) >= 2 else key


def mk_universe():
    """Surname-first MK keys from every roster/tenure file we ship."""
    names = set()
    for path, getter in [
        ("data/k25_tenure.json", lambda d: d.get("tenure", {})),
        ("data/k26_tenure.json", lambda d: d.get("tenure", {})),
        ("data/mk_contacts.json", lambda d: d.get("by_name", {})),
        ("data/k26_contacts.json", lambda d: d.get("by_name", {})),
    ]:
        try:
            names |= set(getter(json.loads(Path(path).read_text("utf-8"))))
        except Exception:
            pass
    return names


def speaker_map(names):
    """given-first protocol speaker -> surname-first MK key."""
    by_display = {}
    for n in names:
        by_display.setdefault(" ".join(disp(n).split()), n)
    # (surname token, first given token) fallback, unambiguous only.
    by_pair = {}
    for n in names:
        d = disp(n).split()
        if len(d) >= 2:
            by_pair.setdefault((d[-1], d[0]), set()).add(n)
    return by_display, {k: next(iter(v)) for k, v in by_pair.items() if len(v) == 1}


def resolve(speaker, by_display, by_pair):
    s = " ".join(str(speaker or "").split())
    if s in by_display:
        return by_display[s]
    t = s.split()
    if len(t) >= 2:
        return by_pair.get((t[-1], t[0]))
    return None


def main():
    names = mk_universe()
    by_display, by_pair = speaker_map(names)
    files = sorted(PROTO_DIR.glob("*.json"))
    if not files:
        print("no parsed protocols — skipping (run build_protocols.py first)")
        return

    # Pass 1: collect MK turns + document frequencies over ALL turns
    # (guests included in df: a term every speaker uses is not signal
    # for anyone).
    docs, df, n_turns = [], Counter(), 0
    per_mk = defaultdict(lambda: {"turns": 0, "words": 0, "sessions": {}})
    candidates = defaultdict(list)  # mk -> [(score terms, record)]
    for f in files:
        doc = json.loads(f.read_text("utf-8"))
        sid = str(doc["id"])
        for ti, t in enumerate(doc.get("turns", [])):
            if t.get("role") not in ("speaker", "chair"):
                continue
            content = words_of(t.get("text", ""))
            if content:
                n_turns += 1
                for term in set(content):
                    df[term] += 1
            key = resolve(t.get("speaker", ""), by_display, by_pair)
            if not key:
                continue
            words = len((t.get("text") or "").split())
            agg = per_mk[key]
            agg["turns"] += 1
            agg["words"] += words
            s = agg["sessions"].setdefault(sid, {"sid": sid, "committee": doc.get("committee", ""),
                                                 "date": doc.get("date", ""), "turns": 0, "words": 0})
            s["turns"] += 1
            s["words"] += words
            if words >= MIN_WORDS and len(content) >= MIN_TERMS:
                candidates[key].append((content, {
                    "sid": sid, "committee": doc.get("committee", ""), "date": doc.get("date", ""),
                    "turn": ti, "role": t["role"], "words": words,
                    # Committee parser predates the << דובר_המשך >>
                    # marker, so continuation markers leak into some
                    # turn texts — strip them (and the name they carry)
                    # from the excerpt; the verbatim text is one click
                    # away in the protocol itself.
                    "excerpt": re.sub(r"<<[^>]*>>", "",
                                      re.sub(r"<<\s*[^>]*>>\s*[^:]{0,40}:", " — ",
                                             " ".join((t.get("text") or "").split())))[:300],
                }))
        docs.append(sid)

    # Pass 2: score candidates now that IDF is known.
    idf = {t: math.log((n_turns + 1) / (c + 1)) + 1 for t, c in df.items()}
    by_mk = {}
    for key, agg in per_mk.items():
        scored = []
        for content, rec in candidates.get(key, []):
            score = sum(idf[t] for t in set(content)) / math.sqrt(len(content))
            scored.append((score, rec))
        scored.sort(key=lambda x: (-x[0], x[1]["date"], x[1]["sid"]))
        highlights, per_session = [], Counter()
        for score, rec in scored:
            if len(highlights) >= TOP_N or per_session[rec["sid"]] >= PER_SESSION_CAP:
                continue
            per_session[rec["sid"]] += 1
            highlights.append({**rec, "score": round(score, 2)})
        appearances = sorted(agg["sessions"].values(),
                             key=lambda s: (-s["words"], s["date"]))
        for a in appearances:
            a["kind"] = "committee"
        for h in highlights:
            h["kind"] = "committee"
        by_mk[key] = {"turns": agg["turns"], "words": agg["words"],
                      "sessions": len(agg["sessions"]),
                      "committee_sessions": len(agg["sessions"]), "archive_sessions": 0,
                      "plenum_sessions": 0,
                      "appearances": appearances,
                      "highlights": highlights[:PICKS_PER_SOURCE]}

    # ---- plenum (דברי הכנסת): merge the parse-time residue
    # (data/plenum_hl/*.json, build_plenum_hl.py). Appearances and
    # highlights arrive pre-scored on a session-level IDF, so they are
    # picked separately and interleaved — scores never compete across
    # corpora. Sessions are counted per source (committee and plenum
    # session ids share one numeric space).
    plenum_files = sorted(PLENUM_HL_DIR.glob("*.json")) if PLENUM_HL_DIR.exists() else []
    plenum_seen = set()
    for f in plenum_files:
        p = json.loads(f.read_text("utf-8"))
        sid = str(p["id"])
        label = f"מליאת הכנסת · ישיבה {p.get('number') or ''}".strip()
        apps_here = {}  # one appearance per MK per sitting, even under name variants
        for speaker, s in (p.get("speakers") or {}).items():
            key = resolve(speaker, by_display, by_pair)
            if not key:
                continue
            e = by_mk.setdefault(key, {"turns": 0, "words": 0, "sessions": 0,
                                       "committee_sessions": 0, "plenum_sessions": 0, "archive_sessions": 0,
                                       "appearances": [], "highlights": []})
            e["turns"] += s.get("turns", 0)
            e["words"] += s.get("words", 0)
            if key in apps_here:
                apps_here[key]["turns"] += s.get("turns", 0)
                apps_here[key]["words"] += s.get("words", 0)
            else:
                app = {"sid": sid, "kind": "plenum", "committee": label,
                       "date": p.get("date", ""), "turns": s.get("turns", 0),
                       "words": s.get("words", 0)}
                apps_here[key] = app
                e["plenum_sessions"] += 1
                e["appearances"].append(app)
            plenum_seen.add(sid)
        for speaker, tops in (p.get("top") or {}).items():
            key = resolve(speaker, by_display, by_pair)
            if not key:
                continue
            e = by_mk.setdefault(key, {"turns": 0, "words": 0, "sessions": 0,
                                       "committee_sessions": 0, "plenum_sessions": 0, "archive_sessions": 0,
                                       "appearances": [], "highlights": []})
            e.setdefault("_plenum_hl", []).extend(
                {**rec, "sid": sid, "kind": "plenum", "committee": label,
                 "date": p.get("date", "")} for rec in tops)
    # ---- committee archive (full-term residue, build_cmt_hl.py):
    # appearances/highlights for every K25 committee sitting NOT in the
    # full-text corpus. Third corpus, own picks; archive rows link to
    # the official document (no in-app reader for these sittings).
    arch_sessions = 0
    if CMT_HL.exists():
        arch = json.loads(CMT_HL.read_text("utf-8"))
        sess_tab = arch.get("sessions", {})
        arch_sessions = len(sess_tab)
        for key, agg in (arch.get("by_mk") or {}).items():
            e = by_mk.setdefault(key, {"turns": 0, "words": 0, "sessions": 0,
                                       "committee_sessions": 0, "plenum_sessions": 0, "archive_sessions": 0,
                                       "appearances": [], "highlights": []})
            have = {a["sid"] for a in e["appearances"] if a.get("kind") == "committee"}
            for sid, turns, words in agg.get("apps", []):
                if sid in have:
                    continue  # full-text corpus owns this sitting
                meta = sess_tab.get(sid) or ["", "", ""]
                e["appearances"].append({"sid": sid, "kind": "committee_archive",
                                         "committee": meta[0], "date": meta[1], "doc": meta[2],
                                         "turns": turns, "words": words})
                e["turns"] += turns
                e["words"] += words
                e["archive_sessions"] += 1
            e["_arch_hl"] = [{**rec, "kind": "committee_archive",
                              "committee": (sess_tab.get(rec["sid"]) or ["", "", ""])[0],
                              "date": (sess_tab.get(rec["sid"]) or ["", "", ""])[1],
                              "doc": (sess_tab.get(rec["sid"]) or ["", "", ""])[2]}
                             for rec in agg.get("top", []) if rec["sid"] not in have]

    for e in by_mk.values():
        p_picks = sorted(e.pop("_plenum_hl", []), key=lambda r: -r["score"])[:PICKS_PER_SOURCE]
        a_picks = sorted(e.pop("_arch_hl", []), key=lambda r: -r["score"])[:PICKS_PER_SOURCE]
        merged, c_hl = [], e["highlights"]
        for i in range(max(len(c_hl), len(p_picks), len(a_picks))):
            if i < len(c_hl):
                merged.append(c_hl[i])
            if i < len(p_picks):
                merged.append(p_picks[i])
            if i < len(a_picks):
                merged.append(a_picks[i])
        e["highlights"] = merged
        e["appearances"].sort(key=lambda a: (-a["words"], a["date"]))
        e["appearances"] = e["appearances"][:12]  # UI shows the top few; counts live in the *_sessions fields
        e["sessions"] = e["committee_sessions"] + e["plenum_sessions"] + e["archive_sessions"]

    payload = {
        "_meta": {
            "method": "TF-IDF over protocol turns: score = sum of IDF over "
                      "distinct content terms / sqrt(content terms); "
                      f"candidates >= {MIN_WORDS} words; max {PER_SESSION_CAP} "
                      "per sitting. No LLM.",
            "sessions": len(docs), "turns_scored": n_turns, "mks": len(by_mk),
            "plenum_sessions": len(plenum_seen), "archive_sessions": arch_sessions,
            "source": "data/protocols/*.json (official committee transcripts) "
                      "+ data/plenum_hl/*.json (plenum residue, build_plenum_hl.py; "
                      "session-level IDF, picked separately — scores are not "
                      "comparable across the two corpora)",
        },
        "by_mk": by_mk,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    total_hl = sum(len(v["highlights"]) for v in by_mk.values())
    print(f"DONE {OUT} — {len(by_mk)} MKs, {total_hl} highlights from "
          f"{len(docs)} protocols ({n_turns} turns scored)")


if __name__ == "__main__":
    main()
