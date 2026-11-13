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
  {date, type: new_vote, kn, vote_id, item_id}
  {date, type: new_session, kn, session_id, committee_id}

First run only seeds the baseline (no event flood for history).
Events are capped at the newest 800. Dates are Asia/Jerusalem.
"""
import json
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
    f = DATA / ("k25_bills.json" if kn == 25 else "k26_bills.json")
    rows = load(f, [])
    return {str(b["id"]): b for b in rows if b.get("id") is not None}


def votes(kn):
    f = DATA / ("k25_votes.json" if kn == 25 else "k26_votes.json")
    rows = load(f, [])
    if isinstance(rows, dict):
        rows = rows.get("votes", [])
    return {str(v.get("id") or v.get("vote_id")): v for v in rows if (v.get("id") or v.get("vote_id"))}


def sessions(kn):
    f = DATA / ("committees.json" if kn == 25 else "k26_committees.json")
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

    new_state = {"bills": {}, "votes": {}, "sessions": {}}
    for kn in (25, 26):
        bs = bills(kn)
        new_state["bills"][str(kn)] = {i: {"s": b.get("status_id"), "t": b.get("status")} for i, b in bs.items()}
        vs = votes(kn)
        new_state["votes"][str(kn)] = sorted(vs.keys())
        ss = sessions(kn)
        new_state["sessions"][str(kn)] = sorted(ss.keys())

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
        old_sess = set(state.get("sessions", {}).get(str(kn), []))
        for sid, s in ss.items():
            if sid not in old_sess:
                new_events.append({"date": (s.get("start") or s.get("date") or today)[:10],
                                   "type": "new_session", "kn": kn, "session_id": sid,
                                   "committee_id": s.get("committee_id"), "committee": s.get("committee")})

    if state is None:
        print("events: baseline seeded, no events emitted")
    else:
        events = (events + new_events)[-CAP:]
        print(f"events: +{len(new_events)} new, {len(events)} total")
    STATE_F.write_text(json.dumps(new_state, ensure_ascii=False), "utf-8")
    EVENTS_F.write_text(json.dumps(events, ensure_ascii=False), "utf-8")


if __name__ == "__main__":
    main()
