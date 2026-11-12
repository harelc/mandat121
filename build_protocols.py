#!/usr/bin/env python3
"""Committee protocol transcripts, parsed into speaker turns.

Source: 'פרוטוקול ועדה' documents in data/committees.json (fs.knesset.gov.il
'.doc' files that are really docx). The docx XML carries explicit turn
markers — << יור >> chair, << דובר >> speaker (faction in parens for MKs),
<< אורח >> guest, << קריאה >> interjection, << נושא >> agenda topic — so
parsing is structural, not guesswork.

Outputs:
  data/protocols/{session_id}.json — {committee, date, speakers, turns[]}
    (shared across Knessets: session IDs are globally unique)
  data/protocols_index.json        — K25 index (index.html loads this)
  data/k26_protocols_index.json    — K26 index, same shape

Usage: python3 build_protocols.py [--knesset 25|26]  (default 25; input is
committees.json for K25, data/k26_committees.json for K26. Missing input
or no protocol documents = exit 0, no write.)
"""
import argparse, json, re, sys, time, urllib.request, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

OUT = Path("data/protocols")
HDR = {"User-Agent": "Mozilla/5.0 mandat121-build"}
MARK = re.compile(r"<<\s*(יו\"ר|יור|דובר|אורח|קריאה|נושא|סיום|דובר_המשך|הפסקה|מנהל)\s*>>\s*(.*?)<<\s*(יו\"ר|יור|דובר|אורח|קריאה|נושא|סיום|דובר_המשך|הפסקה|מנהל)\s*>>", re.S)
ROLE = {"יור": "chair", 'יו"ר': "chair", "דובר": "speaker", "אורח": "guest", "קריאה": "interjection", "נושא": "topic", "סיום": "end", "דובר_המשך": "cont", "הפסקה": "topic", "מנהל": "chair"}

def paras_of(body):
    """Turn text with its paragraphs intact. docx paragraphs arrive as
    lines (docx_text maps </w:p> to \\n); collapsing all whitespace —
    the pre-fix behaviour — fused a whole speech, including every
    same-speaker continuation, into one wall of text. Paragraphs are
    stored joined by a blank line and rendered as separate <p>s."""
    return "\n\n".join(p for p in (" ".join(x.split()) for x in body.split("\n")) if p)

def docx_text(url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HDR)
            with urllib.request.urlopen(req, timeout=90) as r:
                blob = r.read()
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    with zipfile.ZipFile(BytesIO(blob)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return (xml.replace("&quot;", '"').replace("&apos;", "'")
               .replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))

def parse(session, url):
    return parse_text(session, docx_text(url))

def parse_text(session, text):
    turns, speakers = [], {}
    matches = list(MARK.finditer(text))
    for i, m in enumerate(matches):
        role = ROLE[m.group(1)]
        header = " ".join(m.group(2).split()).rstrip(":").strip()
        body = text[m.end(): matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        body = paras_of(re.sub(r"\s*<<\s*סיום\s*>>\s*$", "", body))  # a lone trailing end-marker has no pair to match
        if role == "cont":
            # << דובר_המשך >>: the same speaker continues — another
            # paragraph of the previous turn, not a new turn. (The
            # pre-fix marker set didn't know it, so continuations and
            # the marker itself leaked into the previous turn's text.)
            if turns and turns[-1]["role"] in ("speaker", "chair", "guest") and body:
                turns[-1]["text"] += "\n\n" + body
                prev = speakers.get(turns[-1]["speaker"])
                if prev:
                    prev["words"] += len(body.split())
                continue
            role = "speaker"  # a continuation with nothing before it
        if role == "topic":
            turns.append({"role": "topic", "speaker": "", "text": header or body})
            continue
        if role == "end":
            continue
        faction = ""
        if header.startswith("(") and header.endswith(")"):
            # << מנהל >> headers wrap the whole name: (היו"ר שם, 14:08)
            header = re.sub(r",\s*\d{1,2}:\d{2}\s*$", "", header[1:-1]).strip()
            fm = None
        else:
            fm = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", header)
        if fm:
            header, faction = fm.group(1).strip(), fm.group(2).strip()
        speaker = re.sub(r"^היו[\"״]ר\s+", "", header).strip()
        if not speaker or not body:
            continue
        turns.append({"role": role, "speaker": speaker, "faction_hint": faction, "text": body})
        s = speakers.setdefault(speaker, {"turns": 0, "words": 0, "role": role, "faction_hint": faction})
        s["turns"] += 1
        s["words"] += len(body.split())
    return {"id": session["id"], "committee": session.get("committee", ""),
            "date": (session.get("start") or "")[:10], "speakers": speakers, "turns": turns}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--knesset", type=int, default=25, choices=(25, 26))
    kn = ap.parse_args().knesset
    src = Path("data/committees.json" if kn == 25 else f"data/k{kn}_committees.json")
    if not src.exists():
        print(f"K{kn}: {src} not built yet — skipping protocols build")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    data = json.load(open(src, encoding="utf-8"))
    jobs = []
    for s in data["recent_sessions"]:
        for d in s.get("docs", []):
            if "פרוטוקול" in d.get("group", ""):
                jobs.append((s, d["path"]))
                break
    print(f"{len(jobs)} sessions with protocols", flush=True)
    if not jobs:
        print(f"K{kn}: no protocol documents yet — skipping (index left untouched)")
        return
    # Protocol documents are immutable once published, and each run used to
    # re-download and re-parse all of them (~270 docx, ~30s) to produce
    # byte-identical output. Skip sessions whose parsed file already exists
    # for the same source URL (state: data/protocols_state.json, shared
    # across Knessets — session ids are globally unique); only new or
    # re-published (URL-changed) protocols are fetched.
    idx_prev_path = Path("data/protocols_index.json" if kn == 25 else f"data/k{kn}_protocols_index.json")
    prev_index = {}
    if idx_prev_path.exists():
        try:
            prev_index = json.loads(idx_prev_path.read_text("utf-8"))
        except Exception:
            prev_index = {}
    state_path = Path("data/protocols_state.json")
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text("utf-8"))
        except Exception:
            state = {}
    index, done, failed = {}, 0, []
    todo = []
    for s, u in jobs:
        sid = str(s["id"])
        if state.get(sid) == u and (OUT / f"{s['id']}.json").exists() and sid in prev_index:
            index[sid] = prev_index[sid]
            done += 1
        else:
            todo.append((s, u))
    if todo:
        print(f"  {len(todo)} new/changed to download, {done} reused from disk", flush=True)
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(parse, s, u): (s["id"], u) for s, u in todo}
        for fut in as_completed(futs):
            sid, url = futs[fut]
            try:
                doc = fut.result()
                (OUT / f"{sid}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
                index[str(sid)] = {"turns": len(doc["turns"]), "speakers": len(doc["speakers"])}
                state[str(sid)] = url
            except Exception as e:
                failed.append((sid, str(e)[:80]))
            done += 1
            if done % 50 == 0:
                print(f"{done}/{len(jobs)} done, {len(failed)} failed", flush=True)
    idx_out = "data/protocols_index.json" if kn == 25 else f"data/k{kn}_protocols_index.json"
    json.dump(index, open(idx_out, "w", encoding="utf-8"), ensure_ascii=False)
    state_path.write_text(json.dumps(state, ensure_ascii=False), "utf-8")
    print(f"DONE {len(index)} protocols, failed: {failed[:8]}")

if __name__ == "__main__":
    main()
