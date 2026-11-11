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

Incremental by default: with a previous output + the session index in
data/committees_state.json (data/k{N}_committees_state.json), a run
only refetches sessions started in the last 14 days, committee items
for bills never seen before, and documents for very recent sessions —
a few requests, ~3s, instead of the full ~500-request rebuild. The
full rebuild (also what seeds the state) runs with --full or when no
usable state exists.
Usage: python3 build_committees.py [--knesset 25|26] [--full]  (default 25)
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


def out_path_for(kn):
    return DATA / ("committees.json" if kn == 25 else f"k{kn}_committees.json")


def state_path_for(kn):
    return DATA / ("committees_state.json" if kn == 25 else f"k{kn}_committees_state.json")


def load_json(p):
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return None


def minus_days(iso, days):
    from datetime import datetime, timedelta
    d = datetime.fromisoformat((iso or "")[:10])
    return (d - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")


def incremental_main(kn, recent_since, bill_ids, bill_id_set, cmap, smap, prev, state):
    """Merge-only refresh on top of the previous output + session index.

    The full build makes ~500 OData requests (every bill's committee
    items, every bill-history session, all ~11k sessions for totals) and
    took 5+ minutes even when nothing had changed. Sessions and their
    agenda items are effectively immutable once held, so a daily run
    only needs: sessions started since (watermark - 14d) — refetched
    whole, which also picks up corrections — plus committee items for
    bills the builder has never seen. Everything else is carried over
    from the previous committees.json; per-committee totals and
    last-session dates are recomputed locally from the session index
    kept in the state file. Sessions deleted upstream within the
    overlap window are not detected (same merge assumption as the
    build_knesset vote window).
    """
    idx = state["sessions"]  # str id -> [committee_id, start, type, status, location]
    watermark = state.get("watermark") or ""
    since = minus_days(watermark, 14) if watermark else RECENT_SINCE[kn]
    print(f"incremental: sessions since {since[:10]} (watermark {watermark[:10]})...")
    window = fetch_all("KNS_CommitteeSession", {
        "$filter": f"KnessetNum eq {kn} and StartDate gt {since}",
        "$orderby": "StartDate",
        "$select": "Id,CommitteeID,StartDate,TypeDesc,StatusDesc,Location",
    })
    changed = set()
    for s in window:
        idx[str(s["Id"])] = [s["CommitteeID"], s["StartDate"], s.get("TypeDesc"),
                             s.get("StatusDesc"), s.get("Location")]
        changed.add(s["Id"])
    print(f"  {len(window)} sessions in window, index holds {len(idx)}")

    prev_recent = {s["id"]: s for s in prev.get("recent_sessions", [])}
    recent_ids = sorted((int(sid) for sid, e in idx.items() if (e[1] or "") > recent_since),
                        key=lambda sid: idx[str(sid)][1], reverse=True)
    # Items/docs are refetched for changed or newly-seen recent sessions,
    # and for sessions held in the last 21 days (their documents —
    # protocols — are published days after the session itself).
    from datetime import datetime, timedelta, timezone
    fresh_cut = (datetime.now(timezone.utc) - timedelta(days=21)).strftime("%Y-%m-%dT00:00:00Z")
    fetch_sids = [sid for sid in recent_ids
                  if sid in changed or sid not in prev_recent or idx[str(sid)][1] > fresh_cut]
    print(f"recent sessions: {len(recent_ids)}, refetching items/docs for {len(fetch_sids)}")
    items_by_session, docs_by_session = defaultdict(list), defaultdict(list)
    if fetch_sids:
        for it in fetch_in("KNS_CmtSessionItem", "CommitteeSessionID", fetch_sids,
                           "", "CommitteeSessionID,ItemID,Name,ItemTypeID,StatusID", batch=40):
            items_by_session[it["CommitteeSessionID"]].append(it)
        for d in fetch_in("KNS_DocumentCommitteeSession", "CommitteeSessionID", fetch_sids,
                          "", "CommitteeSessionID,GroupTypeDesc,FilePath", batch=40):
            docs_by_session[d["CommitteeSessionID"]].append(d)
    # Changed sessions outside the recent window still feed bill history:
    # their items are refetched too (docs are not kept for those).
    old_changed = [sid for sid in changed if sid not in items_by_session]
    if old_changed:
        for it in fetch_in("KNS_CmtSessionItem", "CommitteeSessionID", old_changed,
                           "", "CommitteeSessionID,ItemID,Name,ItemTypeID,StatusID", batch=40):
            items_by_session[it["CommitteeSessionID"]].append(it)

    # Per-bill history: drop entries from changed sessions (refetched
    # below), keep the rest, add items for bills never processed before.
    bill_committees = {}
    for bid, entries in prev.get("bill_committees", {}).items():
        kept = [e for e in entries if e["session_id"] not in changed]
        if kept:
            bill_committees[bid] = kept
    item_rows = [it for sid in fetch_sids for it in items_by_session.get(sid, [])]
    bills_seen = set(state.get("bills_seen") or [])
    new_bills = [b for b in bill_ids if b not in bills_seen]
    if new_bills:
        print(f"committee items for {len(new_bills)} new bills...")
        item_rows += fetch_in("KNS_CmtSessionItem", "ItemID", new_bills,
                              "ItemTypeID eq 2", "ItemID,CommitteeSessionID,StatusID,ItemTypeID", batch=40)
    missing_sess = sorted({it["CommitteeSessionID"] for it in item_rows
                           if str(it["CommitteeSessionID"]) not in idx})
    if missing_sess:  # a new bill references a session the index never saw
        for s in fetch_in("KNS_CommitteeSession", "Id", missing_sess,
                          "", "Id,CommitteeID,StartDate,TypeDesc,StatusDesc,Location", batch=40):
            idx[str(s["Id"])] = [s["CommitteeID"], s["StartDate"], s.get("TypeDesc"),
                                 s.get("StatusDesc"), s.get("Location")]
    seen_pairs = {(bid, e["session_id"]) for bid, es in bill_committees.items() for e in es}
    for it in item_rows:
        if it.get("ItemTypeID") != 2:
            continue
        bid = str(it["ItemID"])
        if it["ItemID"] not in bill_id_set or (bid, it["CommitteeSessionID"]) in seen_pairs:
            continue
        e = idx.get(str(it["CommitteeSessionID"]))
        if not e:
            continue
        bill_committees.setdefault(bid, []).append({
            "date": (e[1] or "")[:10], "session_id": it["CommitteeSessionID"],
            "committee_id": e[0], "status_id": it.get("StatusID"),
            "status": smap.get(it.get("StatusID")), "type": e[2],
        })
        seen_pairs.add((bid, it["CommitteeSessionID"]))
    for v in bill_committees.values():
        v.sort(key=lambda x: x["date"])

    totals, last_session, touched = Counter(), {}, defaultdict(set)
    for sid, e in idx.items():
        totals[e[0]] += 1
        if (e[1] or "") > last_session.get(e[0], ""):
            last_session[e[0]] = e[1]
    for bid, entries in bill_committees.items():
        for e in entries:
            touched[e["committee_id"]].add(int(bid))

    recent_sessions = []
    for sid in recent_ids:
        e = idx[str(sid)]
        if sid in fetch_sids:
            items = [{
                "item_id": it["ItemID"], "name": (it.get("Name") or "").strip(),
                "item_type": it.get("ItemTypeID"), "status_id": it.get("StatusID"),
                "status": smap.get(it.get("StatusID")),
                "in_dataset": it["ItemID"] in bill_id_set,
            } for it in items_by_session.get(sid, [])]
            docs = [{"group": d["GroupTypeDesc"], "path": d["FilePath"]}
                    for d in docs_by_session.get(sid, [])]
        else:
            old = prev_recent[sid]
            items = [{**it, "in_dataset": it["item_id"] in bill_id_set}
                     for it in old.get("items", [])]
            docs = old.get("docs", [])
        recent_sessions.append({
            "id": sid, "committee_id": e[0],
            "committee": (cmap.get(e[0]) or {}).get("Name"),
            "start": e[1], "type": e[2], "status": e[3], "location": e[4],
            "items": items, "docs": docs,
        })

    out = {
        "generated_from": prev.get("generated_from", ""),
        "knesset": kn,
        "recent_since": recent_since[:10],
        "status_map": {str(k): v for k, v in smap.items()},
        "committees": [{
            "id": c["Id"], "name": c["Name"], "category": c.get("CategoryDesc"),
            "type": c.get("CommitteeTypeDesc"), "current": c.get("IsCurrent"),
            "sessions_total": totals.get(c["Id"], 0),
            "last_session": (last_session.get(c["Id"]) or "")[:10] or None,
            "dataset_bills": len(touched.get(c["Id"], ())),
        } for c in cmap.values()],
        "bill_committees": bill_committees,
        "recent_sessions": recent_sessions,
    }
    p = out_path_for(kn)
    p.write_text(json.dumps(out, ensure_ascii=False), "utf-8")
    new_state = {
        "sessions": idx,
        "watermark": max((e[1] or "" for e in idx.values()), default=watermark),
        "bills_seen": sorted(bill_id_set),
    }
    state_path_for(kn).write_text(json.dumps(new_state, ensure_ascii=False), "utf-8")
    print(f"\nwrote {p} ({p.stat().st_size/1e6:.1f} MB) [incremental]")
    print(f"committees: {len(cmap)}, bills with committee history: {len(bill_committees)}, "
          f"recent sessions: {len(recent_sessions)}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--knesset", type=int, default=25, choices=(25, 26))
    ap.add_argument("--full", action="store_true",
                    help="ignore previous output/state and rebuild from scratch")
    args = ap.parse_args()
    kn = args.knesset
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

    if not args.full:
        prev = load_json(out_path_for(kn))
        state = load_json(state_path_for(kn))
        if prev and state and state.get("sessions"):
            incremental_main(kn, recent_since, bill_ids, bill_id_set,
                             cmap, smap, prev, state)
            return
        print("no usable previous output/state — falling back to full build")

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
    p = out_path_for(kn)
    p.write_text(json.dumps(out, ensure_ascii=False), "utf-8")
    # Seed the incremental state: every session (id, committee, start),
    # with full row fields overlaid for the sessions fetched in full.
    idx_state = {str(s["Id"]): [s["CommitteeID"], s["StartDate"], None, None, None]
                 for s in all_sessions}
    for s in recent + hist_sessions:
        idx_state[str(s["Id"])] = [s["CommitteeID"], s["StartDate"], s.get("TypeDesc"),
                                   s.get("StatusDesc"), s.get("Location")]
    state_path_for(kn).write_text(json.dumps({
        "sessions": idx_state,
        "watermark": max((s["StartDate"] or "" for s in all_sessions), default=""),
        "bills_seen": sorted(bill_id_set),
    }, ensure_ascii=False), "utf-8")
    print(f"\nwrote {p} ({p.stat().st_size/1e6:.1f} MB)")
    print(f"committees: {len(committees)}, bills with committee history: {len(bill_committees)}, "
          f"recent sessions: {len(recent)} with {len(r_items)} items, {len(r_docs)} docs")


if __name__ == "__main__":
    main()
