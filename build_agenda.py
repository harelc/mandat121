#!/usr/bin/env python3
"""
Build data/agenda.json — "what's on this week's agenda" for המנדט ה־121.

The official knesset.gov.il agenda page joins two things, and so do we —
from the official OData v4 feed only (main.knesset.gov.il is Radware-walled
to scripts; see KNESSET_APIS.md / ROADMAP working rules):

  1. Plenum: KNS_PlenumSession + KNS_PlmSessionItem (the sitting's order
     of the day — bills, agenda motions, queries…), each item linked to
     its bill when the bill is in data/k25_bills.json.
  2. Committees: KNS_CommitteeSession + KNS_CmtSessionItem, with location,
     open/closed type and cancelled status kept verbatim.

Window: recent calendar months (2 back, 1 ahead) ship inline in
data/agenda.json; every month since the Knesset's first session ships as
a data/agenda/YYYY-MM.json shard that the UI fetches on navigation, so
the calendar can scroll back the whole term without one huge file.
OData only creates session rows once the
Knesset publishes them — in recess there may be no row for this week at
all. The file therefore says exactly which week is "this week" and what
the window covers, and the UI shows the latest published week instead of
pretending an unpublished week is empty of business. Coverage is labelled,
never backfilled.

Stdlib only, no auth. Explicit $skip/$top pagination (nextLink hangs on
filtered queries — see KNESSET_APIS.md). Usage: python3 build_agenda.py
"""
import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://knesset.gov.il/OdataV4/ParliamentInfo"
HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 mandat121-build"}
DATA = Path(__file__).parent / "data"
KNESSET = 25
DELAY = 0.12
MONTHS_BACK = 2   # calendar window for the UI's day/week/month views
MONTHS_AHEAD = 1  # future-dated sessions OData has already published


def get(entity, params):
    url = f"{BASE}/{entity}?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def fetch_all(entity, params, page=100):
    out, skip = [], 0
    while True:
        p = dict(params, **{"$top": str(page), "$skip": str(skip)})
        d = get(entity, p)
        v = d.get("value", [])
        out.extend(v)
        print(f"    {entity}: {len(out)} rows", flush=True)
        if len(v) < page:
            return out
        skip += page
        time.sleep(DELAY)


def fetch_in(entity, field, ids, select, batch=40):
    """Fetch rows where field in (ids...), batched (URL length limits)."""
    out = []
    ids = list(ids)
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        filt = f"{field} in ({','.join(str(x) for x in chunk)})"
        out.extend(fetch_all(entity, {"$filter": filt, "$select": select}))
        time.sleep(DELAY)
    return out


def fetch_items_range(entity, field, ids, select):
    """Fetch item rows for many sessions in one range scan.

    Session IDs are issued chronologically, so every session we want sits
    between min(ids) and max(ids): one `ge/le` query paginated server-side
    beats hundreds of `in (...)` queries (measured ~15x faster end-to-end).
    Rows for the odd foreign session inside the range are dropped locally.
    """
    ids = set(ids)
    if not ids:
        return []
    filt = f"{field} ge {min(ids)} and {field} le {max(ids)}"
    rows = fetch_all(entity, {"$filter": filt, "$select": select})
    return [r for r in rows if r[field] in ids]


def sunday_of(d):
    """Israel's week starts Sunday (weekday(): Mon=0 … Sun=6)."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def main():
    today = datetime.now(ZoneInfo("Asia/Jerusalem")).date()
    this_sunday = sunday_of(today)

    def add_months(d, n):
        y, m = divmod(d.year * 12 + (d.month - 1) + n, 12)
        return date(y, m + 1, 1)

    # Recent months ship inline in agenda.json (this month ±), padded to full
    # Sun–Sat weeks. Every earlier month of the Knesset ships as its own
    # shard in data/agenda/YYYY-MM.json, fetched by the UI on navigation —
    # a whole term of committee sittings is far too big for one file.
    first_back = add_months(today, -MONTHS_BACK)
    last_ahead = add_months(today, MONTHS_AHEAD + 1) - timedelta(days=1)
    win_start = sunday_of(first_back)
    win_end = sunday_of(last_ahead) + timedelta(days=7)  # exclusive

    def first_start(entity):
        d = get(entity, {"$filter": f"KnessetNum eq {KNESSET}",
                         "$orderby": "StartDate asc", "$top": "1", "$select": "StartDate"})
        v = d.get("value", [])
        return v[0]["StartDate"][:10] if v else today.isoformat()

    earliest = min(first_start("KNS_PlenumSession"), first_start("KNS_CommitteeSession"))
    full_start = sunday_of(add_months(date.fromisoformat(earliest), 0))
    lo = f"{full_start.isoformat()}T00:00:00Z"
    hi = f"{win_end.isoformat()}T00:00:00Z"
    print(f"today (Israel): {today} · this week: {this_sunday} – {this_sunday + timedelta(days=6)}")
    print(f"inline window: {win_start} – {win_end - timedelta(days=1)} · full range from {full_start} (earliest session {earliest})")

    bills = json.loads((DATA / "k25_bills.json").read_text("utf-8"))
    bill_ids = {b["id"] for b in bills if b.get("id")}
    print(f"bills in dataset: {len(bill_ids)}")

    print("statuses...")
    statuses = fetch_all("KNS_Status", {"$select": "Id,Desc"})
    smap = {s["Id"]: s["Desc"] for s in statuses}

    print("committees...")
    committees = fetch_all("KNS_Committee", {
        "$filter": f"KnessetNum eq {KNESSET}",
        "$select": "Id,Name",
    })
    cmap = {c["Id"]: c["Name"] for c in committees}

    print("plenum sessions...")
    plenum = fetch_all("KNS_PlenumSession", {
        "$filter": f"KnessetNum eq {KNESSET} and StartDate ge {lo} and StartDate lt {hi}",
        "$orderby": "StartDate asc",
        "$select": "Id,Number,StartDate,FinishDate,IsSpecialMeeting",
    })
    print(f"  {len(plenum)} plenum sessions")
    p_items = fetch_items_range("KNS_PlmSessionItem", "PlenumSessionID", [s["Id"] for s in plenum],
                                "Id,PlenumSessionID,ItemID,Name,ItemTypeID,ItemTypeDesc,StatusID,Ordinal") if plenum else []
    p_by_session = defaultdict(list)
    for it in p_items:
        p_by_session[it["PlenumSessionID"]].append(it)

    print("committee sessions...")
    csessions = fetch_all("KNS_CommitteeSession", {
        "$filter": f"KnessetNum eq {KNESSET} and StartDate ge {lo} and StartDate lt {hi}",
        "$orderby": "StartDate asc",
        "$select": "Id,CommitteeID,StartDate,TypeDesc,StatusDesc,Location",
    })
    print(f"  {len(csessions)} committee sessions")
    c_items = fetch_items_range("KNS_CmtSessionItem", "CommitteeSessionID", [s["Id"] for s in csessions],
                                "CommitteeSessionID,ItemID,Name,ItemTypeID,StatusID") if csessions else []
    c_by_session = defaultdict(list)
    for it in c_items:
        c_by_session[it["CommitteeSessionID"]].append(it)

    def item(it):
        return {
            "item_id": it["ItemID"], "name": (it.get("Name") or "").strip(),
            "item_type": it.get("ItemTypeDesc"), "status": smap.get(it.get("StatusID")),
            "in_dataset": it.get("ItemTypeID") == 2 and it["ItemID"] in bill_ids,
        }

    plenum_out = [{
        "id": s["Id"], "number": s.get("Number"), "start": s["StartDate"],
        "finish": s.get("FinishDate"), "special": bool(s.get("IsSpecialMeeting")),
        # $orderby=Ordinal is broken in this OData (KNESSET_APIS.md) — sort here
        "items": [item(it) for it in sorted(p_by_session.get(s["Id"], []),
                                            key=lambda x: (x.get("Ordinal") or 0, x.get("Id", 0)))],
    } for s in plenum]
    csess_out = [{
        "id": s["Id"], "committee_id": s["CommitteeID"],
        "committee": cmap.get(s["CommitteeID"]),
        "start": s["StartDate"], "type": s.get("TypeDesc"),
        "status": s.get("StatusDesc"), "location": s.get("Location"),
        "items": [item(it) for it in c_by_session.get(s["Id"], [])],
    } for s in csessions]

    # All months in the full range, and which of them ship inline.
    months, d = [], full_start
    while d < win_end:
        m = d.isoformat()[:7]
        if m not in months:
            months.append(m)
        d += timedelta(days=7)
    if (win_end - timedelta(days=1)).isoformat()[:7] not in months:
        months.append((win_end - timedelta(days=1)).isoformat()[:7])
    # Only months FULLY inside the inline window count as loaded: the window
    # is week-padded, so its first/last months are partial (e.g. inline from
    # Aug 30 must not mark August loaded — its shard still has Aug 3–29).
    def month_first(m):
        y, mo = map(int, m.split("-"))
        return date(y, mo, 1)
    inline_months = [m for m in months
                     if month_first(m) >= win_start and add_months(month_first(m), 1) <= win_end]
    in_inline = lambda s: s["start"][:10] >= win_start.isoformat() and s["start"][:10] < win_end.isoformat()

    meta = {
        "generated_from": "Knesset OData v4: KNS_PlenumSession, KNS_PlmSessionItem, KNS_CommitteeSession, KNS_CmtSessionItem, KNS_Status",
        "knesset": KNESSET,
        "generated_at": datetime.now(ZoneInfo("Asia/Jerusalem")).isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "this_week": [this_sunday.isoformat(), (this_sunday + timedelta(days=6)).isoformat()],
        "window": [full_start.isoformat(), (win_end - timedelta(days=1)).isoformat()],
        "months": months,
        "loaded_months": inline_months,
    }
    p = DATA / "agenda.json"
    p.write_text(json.dumps({**meta,
                             "plenum": [s for s in plenum_out if in_inline(s)],
                             "committees": [s for s in csess_out if in_inline(s)]},
                            ensure_ascii=False), "utf-8")
    print(f"\nwrote {p} ({p.stat().st_size/1e3:.0f} KB, inline months {inline_months})")

    shard_dir = DATA / "agenda"
    shard_dir.mkdir(exist_ok=True)
    for old in shard_dir.glob("*.json"):
        old.unlink()
    total_shard = 0
    for m in months:
        shard = {"month": m,
                 "plenum": [s for s in plenum_out if s["start"][:7] == m],
                 "committees": [s for s in csess_out if s["start"][:7] == m]}
        (shard_dir / f"{m}.json").write_text(json.dumps(shard, ensure_ascii=False), "utf-8")
        total_shard += len(shard["plenum"]) + len(shard["committees"])
    print(f"wrote {len(months)} month shards to {shard_dir} ({total_shard} sessions)")
    print(f"plenum: {len(plenum)} sessions / {len(p_items)} items · "
          f"committees: {len(csessions)} sessions / {len(c_items)} items")


if __name__ == "__main__":
    main()
