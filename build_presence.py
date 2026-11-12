#!/usr/bin/env python3
"""Knesset-26 presence history: arrival order + per-MK workday attendance.

The MK-lobby API (see build_mk_contacts.py) reports only `IsPresent` —
who is in the building *right now*. It has no timestamps and no history,
so an arrival order can only be built by polling it: the first poll of a
workday (Sun–Thu, Asia/Jerusalem) in which an MK reads present is, to
poll resolution, when they arrived. This script is ONE poll. Run it
every ~10 minutes on workdays (`.github/workflows/presence-poll.yml`;
also once per knesset-daily run as a coarse fallback) and it merges the
reading into `data/k26_presence.json`:

  days:  {"2026-11-12": {first_poll, last_poll, arrivals:
             [{name, mk_id, first_seen, at_first_poll}]}}
  by_mk: {name: {mk_id, days, dates[]}}   — derived, recomputed per write

Honesty rules (same as the live overlay in index.html):
- Nothing is backfilled. `tracking_since` is the first day ever polled;
  days before it simply do not exist, and the UI must say so.
- `first_seen` is the first poll that saw the MK, not a turnstile time:
  it is labelled "נראה/תה לראשונה" and ties share a timestamp.
- `at_first_poll` marks MKs already present when that day's polling
  started — their arrival was at or before that time, order unknown.
- An MK who is present, leaves, and returns keeps the first time.
- Only polls that add an arrival (or open a new day) are written, like
  build_mk_contacts.py's write-on-change: the file is the durable
  history, not a poll log, so `last_poll` is the last *merged* poll.
- K25 is never touched: the lobby feed serves only the current (K26)
  roster, so a K25 presence history cannot exist and none is faked.

Offline/testing: `--input lobby.json` reads a saved lobby payload
instead of the network, `--at 2026-11-12T08:30:00+02:00` overrides the
poll instant, `--allow-any-day` records Fri/Sat polls (manual checks).
Stdlib only. No auth, no secrets. Only the boolean signal, the MK's
official MkId and our surname-first name key are stored (no contact
fields — those live in build_mk_contacts.py's snapshot).
"""
import argparse
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

URL = "https://www.knesset.gov.il/WebSiteApi/knessetapi/MkLobby/GetMkLobbyData120?lang=he"
HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 mandat121-build"}
OUT = Path("data/k26_presence.json")
IL = ZoneInfo("Asia/Jerusalem")
WORKDAYS = {6, 0, 1, 2, 3}  # datetime.weekday(): Sun=6, Mon=0 … Thu=3


def empty_state():
    return {"_meta": {}, "days": {}, "by_mk": {}}


def derive_by_mk(days):
    """Per-MK workday counts, recomputed from `days` so the stored
    histogram can never drift from the arrival lists it summarises."""
    by_mk = {}
    for date in sorted(days):
        for a in days[date].get("arrivals", []):
            e = by_mk.setdefault(a["name"], {"mk_id": a.get("mk_id"), "days": 0, "dates": []})
            if a.get("mk_id"):
                e["mk_id"] = a["mk_id"]
            e["days"] += 1
            e["dates"].append(date)
    return by_mk


def merge_poll(state, mks, at, force=False):
    """Merge one lobby reading taken at aware datetime `at` (any tz).

    `mks` is the raw payload list. Returns (state, new_arrivals) where
    new_arrivals is the list of names first seen in this poll. Mutates
    and returns `state`. Non-workdays (Asia/Jerusalem) merge nothing
    unless `force` (manual checks via --allow-any-day).
    """
    local = at.astimezone(IL)
    if not force and local.weekday() not in WORKDAYS:
        return state, []
    date = local.date().isoformat()
    iso = local.isoformat(timespec="seconds")
    days = state.setdefault("days", {})
    day = days.get(date)
    first_poll_of_day = day is None
    if first_poll_of_day:
        day = days[date] = {"first_poll": iso, "last_poll": iso, "arrivals": []}
    known = {a["name"] for a in day["arrivals"]}
    new = []
    for m in mks or []:
        if not m.get("IsPresent"):
            continue
        name = " ".join(f"{m.get('Lastname') or ''} {m.get('Firstname') or ''}".split())
        if not name or name in known:
            continue
        known.add(name)
        day["arrivals"].append({
            "name": name,
            "mk_id": m.get("MkId"),
            "first_seen": iso,
            "at_first_poll": first_poll_of_day,
        })
        new.append(name)
    if new or first_poll_of_day:
        day["last_poll"] = iso
        day["arrivals"].sort(key=lambda a: (a["first_seen"], a["name"]))
    state["by_mk"] = derive_by_mk(days)
    state["_meta"] = {
        "source": URL,
        "knesset": 26,
        "tracking_since": min(days) if days else None,
        "updated_at": day["last_poll"],
        "workdays_tracked": len(days),
        "note": "first_seen = first poll (Sun-Thu, Asia/Jerusalem) that "
                "saw the MK present in the lobby feed; not a turnstile "
                "time. No history exists before tracking_since.",
    }
    return state, new


def fetch_lobby():
    req = urllib.request.Request(URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8")).get("mks", [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="saved lobby payload JSON (offline)")
    ap.add_argument("--at", help="poll instant, ISO 8601 (default: now)")
    ap.add_argument("--allow-any-day", action="store_true",
                    help="record even on Fri/Sat (manual checks only)")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    at = datetime.fromisoformat(args.at) if args.at else datetime.now(IL)
    if at.tzinfo is None:
        at = at.replace(tzinfo=IL)
    if not args.allow_any_day and at.astimezone(IL).weekday() not in WORKDAYS:
        print(f"SKIP {at.astimezone(IL).date()} is not a Sun-Thu workday — no poll recorded")
        return 0

    if args.input:
        mks = json.loads(Path(args.input).read_text("utf-8")).get("mks", [])
    else:
        mks = fetch_lobby()
    if not mks:
        print("SKIP empty lobby payload — keeping previous state", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    state = empty_state()
    if out_path.exists():
        try:
            state = json.loads(out_path.read_text("utf-8"))
        except Exception:
            print(f"WARN {out_path} unreadable — starting fresh", file=sys.stderr)
    before = json.dumps(state.get("days", {}), ensure_ascii=False, sort_keys=True)
    state, new = merge_poll(state, mks, at, force=args.allow_any_day)
    after = json.dumps(state.get("days", {}), ensure_ascii=False, sort_keys=True)
    if before == after:
        print(f"UNCHANGED {out_path} — no new arrivals at {at.astimezone(IL).isoformat(timespec='seconds')}")
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    day = at.astimezone(IL).date().isoformat()
    print(f"DONE {out_path} — {day}: {len(new)} new arrival(s) "
          f"({', '.join(new[:5])}{'…' if len(new) > 5 else ''}), "
          f"{len(state['days'][day]['arrivals'])} so far today, "
          f"{state['_meta']['workdays_tracked']} workday(s) tracked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
