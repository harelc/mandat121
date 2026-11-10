#!/usr/bin/env python3
"""
Build data/committees.json for the המנדט ה־121 (index.html).

What the plenum-only bills.json cannot see, from the official OData v4 feed:
  1. Per-bill committee history: every KNS_CmtSessionItem (ItemTypeID=2,
     ItemID = KNS_Bill.Id) for the bills in data/bills.json, joined to its
     KNS_CommitteeSession (committee, date, open/closed) and to the bill
     status at that moment (item StatusID -> KNS_Status).
  2. Recent committee activity feed: all sessions since RECENT_SINCE with
     their agenda items and document types — committee work continues
     months after the plenum stops voting.
  3. Committee roster + per-committee session totals for the Knesset.

Stdlib only, no auth. Explicit $skip/$top pagination (nextLink hangs on
filtered queries — see KNESSET_APIS.md).
Usage: python3 build_committees.py [--knesset 25|26]  (default 25)
Output: data/committees.json for K25 (the file index.html loads today),
data/k26_committees.json for K26 — never clobber the K25 file. No
committees published for N yet = exit 0, no write.
"""
import argparse
import json
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

BASE = "https://knesset.gov.il/OdataV4/ParliamentInfo"
HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 mandat121-build"}
DATA = Path(__file__).parent / "data"
# "Recent" window per Knesset: K25 = bills.json vote cutoff; K26 = era start
# (build_knesset.ERA_START) so every K26 session counts as recent at first.
RECENT_SINCE = {25: "2026-06-24T00:00:00Z", 26: "2026-10-01T00:00:00Z"}
DELAY = 0.12


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
        print(f"    {entity}: {len(out)} rows (skip={skip})", flush=True)
        if len(v) < page:
            return out
        skip += page
        time.sleep(DELAY)


def fetch_in(entity, field, ids, extra_filter, select, batch=15):
    """Fetch rows where field in (ids...), batched (URL length limits)."""
    out = []
    ids = list(ids)
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        filt = f"{field} in ({','.join(str(x) for x in chunk)})"
        if extra_filter:
            filt += f" and {extra_filter}"
        out.extend(fetch_all(entity, {"$filter": filt, "$select": select}))
        time.sleep(DELAY)
    return out


def bill_ids_for(kn):
    if kn == 25:  # legacy dataset, as before
        bills = json.loads((DATA / "bills.json").read_text("utf-8"))
        return sorted({b["bill_id"] for b in bills if b.get("knesset_num") == 25 and b.get("bill_id")})
    p = DATA / f"k{kn}_bills.json"  # build_knesset.py output: [{"id": ...}]
    if not p.exists():
        return []
    return sorted({b["id"] for b in json.loads(p.read_text("utf-8")) if b.get("id")})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--knesset", type=int, default=25, choices=(25, 26))
    kn = ap.parse_args().knesset
    recent_since = RECENT_SINCE[kn]
    bill_ids = bill_ids_for(kn)
    bill_id_set = set(bill_ids)
    print(f"bills in dataset: {len(bill_ids)}")

    print("committees...")
    committees = fetch_all("KNS_Committee", {
        "$filter": f"KnessetNum eq {kn}",
        "$select": "Id,Name,CategoryDesc,CommitteeTypeDesc,IsCurrent",
    })
    if not committees:
        print(f"K{kn}: no committees published yet — skipping committees build")
        return
    cmap = {c["Id"]: c for c in committees}

    print("bill statuses...")
    statuses = fetch_all("KNS_Status", {
        "$filter": "TypeID eq 2", "$select": "Id,Desc",
    })
    smap = {s["Id"]: s["Desc"] for s in statuses}

    print("per-bill committee items (batched)...")
    items = fetch_in("KNS_CmtSessionItem", "ItemID", bill_ids,
                     "ItemTypeID eq 2", "ItemID,CommitteeSessionID,StatusID")
    print(f"  {len(items)} bill-session items")

    print("recent committee sessions...")
    recent = fetch_all("KNS_CommitteeSession", {
        "$filter": f"KnessetNum eq {kn} and StartDate gt {recent_since}",
        "$orderby": "StartDate desc",
        "$select": "Id,CommitteeID,StartDate,TypeDesc,StatusDesc,Location",
    })
    print(f"  {len(recent)} sessions since {recent_since[:10]}")
    recent_ids = [s["Id"] for s in recent]
    r_items = fetch_in("KNS_CmtSessionItem", "CommitteeSessionID", recent_ids,
                       "", "CommitteeSessionID,ItemID,Name,ItemTypeID,StatusID")
    r_docs = fetch_in("KNS_DocumentCommitteeSession", "CommitteeSessionID", recent_ids,
                      "", "CommitteeSessionID,GroupTypeDesc,FilePath")
    items_by_session = defaultdict(list)
    for it in r_items:
        items_by_session[it["CommitteeSessionID"]].append(it)
    docs_by_session = defaultdict(list)
    for d in r_docs:
        docs_by_session[d["CommitteeSessionID"]].append(d)

    # Sessions referenced by our bills (may predate RECENT_SINCE)
    need_sessions = {it["CommitteeSessionID"] for it in items} - set(recent_ids)
    print(f"fetching {len(need_sessions)} bill-history sessions...")
    hist_sessions = fetch_in("KNS_CommitteeSession", "Id", sorted(need_sessions),
                             "", "Id,CommitteeID,StartDate,TypeDesc,StatusDesc,Location")
    sess = {s["Id"]: s for s in recent + hist_sessions}

    print("per-committee session totals (all K25)...")
    all_sessions = fetch_all("KNS_CommitteeSession", {
        "$filter": f"KnessetNum eq {kn}", "$select": "CommitteeID,StartDate",
    })
    totals = Counter(s["CommitteeID"] for s in all_sessions)
    last_session = {}
    for s in all_sessions:
        cid = s["CommitteeID"]
        if s["StartDate"] > last_session.get(cid, ""):
            last_session[cid] = s["StartDate"]

    bill_committees = defaultdict(list)
    touched = defaultdict(set)
    for it in items:
        s = sess.get(it["CommitteeSessionID"])
        if not s:
            continue
        bill_committees[str(it["ItemID"])].append({
            "date": (s["StartDate"] or "")[:10],
            "session_id": s["Id"],
            "committee_id": s["CommitteeID"],
            "status_id": it.get("StatusID"),
            "status": smap.get(it.get("StatusID")),
            "type": s.get("TypeDesc"),
        })
        touched[s["CommitteeID"]].add(it["ItemID"])
    for v in bill_committees.values():
        v.sort(key=lambda x: x["date"])

    out = {
        "generated_from": "Knesset OData v4: KNS_Committee(Session), KNS_CmtSessionItem, KNS_DocumentCommitteeSession, KNS_Status",
        "knesset": kn,
        "recent_since": recent_since[:10],
        "status_map": {str(k): v for k, v in smap.items()},
        "committees": [{
            "id": c["Id"], "name": c["Name"], "category": c.get("CategoryDesc"),
            "type": c.get("CommitteeTypeDesc"), "current": c.get("IsCurrent"),
            "sessions_total": totals.get(c["Id"], 0),
            "last_session": (last_session.get(c["Id"]) or "")[:10] or None,
            "dataset_bills": len(touched.get(c["Id"], ())),
        } for c in committees],
        "bill_committees": dict(bill_committees),
        "recent_sessions": [{
            "id": s["Id"], "committee_id": s["CommitteeID"],
            "committee": (cmap.get(s["CommitteeID"]) or {}).get("Name"),
            "start": s["StartDate"], "type": s.get("TypeDesc"),
            "status": s.get("StatusDesc"), "location": s.get("Location"),
            "items": [{
                "item_id": it["ItemID"], "name": (it.get("Name") or "").strip(),
                "item_type": it.get("ItemTypeID"), "status_id": it.get("StatusID"),
                "status": smap.get(it.get("StatusID")),
                "in_dataset": it["ItemID"] in bill_id_set,
            } for it in items_by_session.get(s["Id"], [])],
            "docs": [{"group": d["GroupTypeDesc"], "path": d["FilePath"]}
                     for d in docs_by_session.get(s["Id"], [])],
        } for s in recent],
    }
    p = DATA / ("committees.json" if kn == 25 else f"k{kn}_committees.json")
    p.write_text(json.dumps(out, ensure_ascii=False), "utf-8")
    print(f"\nwrote {p} ({p.stat().st_size/1e6:.1f} MB)")
    print(f"committees: {len(committees)}, bills with committee history: {len(bill_committees)}, "
          f"recent sessions: {len(recent)} with {len(r_items)} items, {len(r_docs)} docs")


if __name__ == "__main__":
    main()
