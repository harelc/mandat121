#!/usr/bin/env python3
"""Server-side event stream: diff today's data snapshot against the
previous one and append dated, provable events to data/events.json.

Why: the client only ever sees a bill's CURRENT status — it cannot know
when the status changed or what it changed from (OData's updated field
moves on every sync and proves nothing). This builder runs in the daily
workflow AFTER the data builders, keeping data/events_state.json (the
previous snapshot: bill statuses, seen vote ids, seen session ids) and
emitting events like:
  {date, type: status_change, kn, bill_id, from, to}
  {date, type: new_bill, kn, bill_id}
  {date, type: new_bill_doc, kn, bill_id, docs: [group labels]}
  {date, type: new_vote, kn, vote_id, item_id}
  {date, type: new_session, kn, session_id, committee_id}

new_bill_doc diffs data/k{kn}_bill_docs.json by document identity
(URL basename — the fs.knesset.gov.il filename embeds the document
id). It fires when a new נוסח/document is published for a bill even
if its status_id did not move (e.g. a committee draft during
"הכנה לקריאה ראשונה"), which the status diff cannot see. The docs
baseline seeds silently on the first run that has docs data.

First run only seeds the baseline (no event flood for history).
Events are capped at the newest 800. Dates are Asia/Jerusalem.
"""
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DATA = Path(__file__).parent / "data"
STATE_F = DATA / "events_state.json"
EVENTS_F = DATA / "events.json"
CAP = 800


def load(p, default):
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return default


def bills(kn):
    f = DATA / f"k{kn}_bills.json"
    rows = load(f, [])
    return {str(b["id"]): b for b in rows if b.get("id") is not None}


def bill_docs(kn):
    """{bill_id: {doc_basename: group_label}} from k{kn}_bill_docs.json."""
    d = load(DATA / f"k{kn}_bill_docs.json", {})
    out = {}
    if not isinstance(d, dict):
        return out
    for bid, docs in d.items():
        m = {}
        for x in docs or []:
            if isinstance(x, dict) and x.get("url"):
                m[x["url"].rsplit("/", 1)[-1]] = x.get("group") or ""
        if m:
            out[str(bid)] = m
    return out


def new_doc_events(old_docs, docs_now, names, kn, today):
    """new_bill_doc events for docs present now but absent in the
    previous snapshot. old_docs: {bill_id: [basenames]} (state form)."""
    out = []
    for bid, m in docs_now.items():
        added = [g for base, g in sorted(m.items())
                 if base not in set(old_docs.get(bid, []))]
        labels = list(dict.fromkeys(g for g in added if g))
        if added:
            out.append({"date": today, "type": "new_bill_doc", "kn": kn,
                        "bill_id": bid, "name": names.get(bid),
                        "docs": labels})
    return out


def votes(kn):
    f = DATA / f"k{kn}_votes.json"
    rows = load(f, [])
    if isinstance(rows, dict):
        rows = rows.get("votes", [])
    return {str(v.get("id") or v.get("vote_id")): v for v in rows if (v.get("id") or v.get("vote_id"))}


def sessions(kn):
    f = DATA / ("committees.json" if kn == 25 else f"k{kn}_committees.json")
    d = load(f, {})
    out = {}
    for s in d.get("recent_sessions", []) if isinstance(d, dict) else []:
        if s.get("id") is not None:
            out[str(s["id"])] = s
    return out


def main():
    today = datetime.now(ZoneInfo("Asia/Jerusalem")).date().isoformat()
    state = load(STATE_F, None)
    events = load(EVENTS_F, [])
    new_events = []

    new_state = {"bills": {}, "votes": {}, "sessions": {}, "docs": {}}
    docs_baseline = state is not None and "docs" not in state
    # Eras on disk, not a frozen (25, 26) tuple: a new era's files
    # appearing is enough for its events to start flowing (state is
    # keyed per era, so a new era seeds cleanly without touching others).
    eras = {25}
    for f in DATA.glob("k*_bills.json"):
        m = re.match(r"k(\d+)_bills\.json", f.name)
        if m:
            eras.add(int(m.group(1)))
    for kn in sorted(eras):
        bs = bills(kn)
        new_state["bills"][str(kn)] = {i: {"s": b.get("status_id"), "t": b.get("status")} for i, b in bs.items()}
        vs = votes(kn)
        new_state["votes"][str(kn)] = sorted(vs.keys())
        ss = sessions(kn)
        new_state["sessions"][str(kn)] = sorted(ss.keys())
        docs_now = bill_docs(kn)
        new_state["docs"][str(kn)] = {i: sorted(m) for i, m in docs_now.items()}

        if state is None:
            continue  # baseline seed only
        old_bills = state.get("bills", {}).get(str(kn), {})
        for i, b in bs.items():
            if i not in old_bills:
                new_events.append({"date": today, "type": "new_bill", "kn": kn, "bill_id": i,
                                   "name": b.get("name"), "status": b.get("status")})
            elif old_bills[i].get("s") != b.get("status_id"):
                new_events.append({"date": today, "type": "status_change", "kn": kn, "bill_id": i,
                                   "name": b.get("name"), "from": old_bills[i].get("t"), "to": b.get("status")})
        old_votes = set(state.get("votes", {}).get(str(kn), []))
        for vid, v in vs.items():
            if vid not in old_votes:
                new_events.append({"date": v.get("date") or today, "type": "new_vote", "kn": kn,
                                   "vote_id": vid, "item_id": v.get("item_id"),
                                   "for": v.get("for"), "against": v.get("against")})
        if state is not None and not docs_baseline:
            old_docs = state.get("docs", {}).get(str(kn), {})
            names = {i: b.get("name") for i, b in bs.items()}
            new_events += new_doc_events(old_docs, docs_now, names, kn, today)
        old_sess = set(state.get("sessions", {}).get(str(kn), []))
        for sid, s in ss.items():
            if sid not in old_sess:
                new_events.append({"date": (s.get("start") or s.get("date") or today)[:10],
                                   "type": "new_session", "kn": kn, "session_id": sid,
                                   "committee_id": s.get("committee_id"), "committee": s.get("committee")})

    if docs_baseline:
        print("events: docs baseline seeded silently (first run with docs data)")
    if state is None:
        print("events: baseline seeded, no events emitted")
    else:
        events = (events + new_events)[-CAP:]
        print(f"events: +{len(new_events)} new, {len(events)} total")
    STATE_F.write_text(json.dumps(new_state, ensure_ascii=False), "utf-8")
    EVENTS_F.write_text(json.dumps(events, ensure_ascii=False), "utf-8")


if __name__ == "__main__":
    main()
