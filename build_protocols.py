#!/usr/bin/env python3
"""Committee protocol transcripts, parsed into speaker turns.

Source: 'פרוטוקול ועדה' documents in data/committees.json (fs.knesset.gov.il
'.doc' files that are really docx). The docx XML carries explicit turn
markers — << יור >> chair, << דובר >> speaker (faction in parens for MKs),
<< אורח >> guest, << קריאה >> interjection, << נושא >> agenda topic — so
parsing is structural, not guesswork.

Outputs:
  data/protocols/{session_id}.json — {committee, date, speakers, turns[]}
  data/protocols_index.json        — {session_id: {turns, speakers, mks}}
"""
import json, re, sys, time, urllib.request, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

OUT = Path("data/protocols")
HDR = {"User-Agent": "Mozilla/5.0 mandat121-build"}
MARK = re.compile(r"<<\s*(יור|דובר|אורח|קריאה|נושא|סיום)\s*>>\s*(.*?)<<\s*(יור|דובר|אורח|קריאה|נושא|סיום)\s*>>", re.S)
ROLE = {"יור": "chair", "דובר": "speaker", "אורח": "guest", "קריאה": "interjection", "נושא": "topic", "סיום": "end"}

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
    text = docx_text(url)
    turns, speakers = [], {}
    matches = list(MARK.finditer(text))
    for i, m in enumerate(matches):
        role = ROLE[m.group(1)]
        header = " ".join(m.group(2).split()).rstrip(":").strip()
        body = text[m.end(): matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        body = " ".join(body.split()).strip()
        if role == "topic":
            turns.append({"role": "topic", "speaker": "", "text": header or body})
            continue
        if role == "end":
            continue
        faction = ""
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
    OUT.mkdir(parents=True, exist_ok=True)
    data = json.load(open("data/committees.json", encoding="utf-8"))
    jobs = []
    for s in data["recent_sessions"]:
        for d in s.get("docs", []):
            if "פרוטוקול" in d.get("group", ""):
                jobs.append((s, d["path"]))
                break
    print(f"{len(jobs)} sessions with protocols", flush=True)
    index, done, failed = {}, 0, []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(parse, s, u): s["id"] for s, u in jobs}
        for fut in as_completed(futs):
            sid = futs[fut]
            try:
                doc = fut.result()
                (OUT / f"{sid}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
                index[str(sid)] = {"turns": len(doc["turns"]), "speakers": len(doc["speakers"])}
            except Exception as e:
                failed.append((sid, str(e)[:80]))
            done += 1
            if done % 50 == 0:
                print(f"{done}/{len(jobs)} done, {len(failed)} failed", flush=True)
    json.dump(index, open("data/protocols_index.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(f"DONE {len(index)} protocols, failed: {failed[:8]}")

if __name__ == "__main__":
    main()
