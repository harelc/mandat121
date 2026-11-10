#!/usr/bin/env python3
"""Removed bill initiators/sponsors, from KNS_BillHistoryInitiator.

build_knesset.py stores the *current* KNS_BillInitiator snapshot. People
who signed a bill and were later removed — appointed minister/deputy
(their private bills drop), left the Knesset, withdrew — exist only in
KNS_BillHistoryInitiator (EndDate + ReasonDesc). Some bills end up with
no current initiator at all for exactly this reason.

Usage: python3 build_k25_bill_history.py [--knesset 25|26]  (default 25)
Output: data/k{N}_bill_history.json {bill_id: [{name, initiator, from,
to, reason}]}. Nothing published for N yet = exit 0, no write.
"""
import argparse, json, sys, urllib.parse, urllib.request
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).parent / "data"
V4 = "https://knesset.gov.il/OdataV4/ParliamentInfo/"
SVC = "https://knesset.gov.il/Odata/ParliamentInfo.svc/"  # V4 400s on KNS_Person; svc serves it
HDR = {"Accept": "application/json", "User-Agent": "biapia-research/1.0 (bill-history build)"}


def get(base, entity, params):
    url = f"{base.rstrip('/')}/{entity}?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HDR)
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if attempt == 2:
                raise
            import time
            time.sleep(2 * (attempt + 1))


def fetch_all(base, entity, params, page=100, label=""):
    out, skip = [], 0
    while True:
        d = get(base, entity, dict(params, **{"$top": str(page), "$skip": str(skip)}))
        v = d.get("value", [])
        out.extend(v)
        if len(v) < page:
            print(f"  {label or entity}: {len(out)} rows", flush=True)
            return out
        skip += page


def fetch_in(base, entity, field, ids, select, batch=50, label=""):
    out, ids = [], list(ids)
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        filt = f"{field} in ({','.join(str(x) for x in chunk)})"
        out.extend(fetch_all(base, entity, {"$filter": filt, "$select": select}, label=""))
    print(f"  {label}: {len(out)} rows total", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--knesset", type=int, default=25, choices=(25, 26))
    kn = ap.parse_args().knesset

    bills_path = DATA / f"k{kn}_bills.json"
    if not bills_path.exists():
        print(f"K{kn}: no bills file yet — skipping bill-history build")
        return
    bill_ids = [b["id"] for b in json.loads(bills_path.read_text("utf-8"))]
    if not bill_ids:
        print(f"K{kn}: bills file empty — skipping bill-history build")
        return

    rows = fetch_in(V4, "KNS_BillHistoryInitiator", "BillID", bill_ids,
                    "BillID,PersonID,IsInitiator,StartDate,EndDate,ReasonDesc",
                    label="history initiators")
    if not rows:
        print(f"K{kn}: no bill-history rows published yet — skipping")
        return

    pids = sorted({r["PersonID"] for r in rows if r.get("PersonID")})
    persons = {}
    for p in fetch_in(V4, "KNS_Person", "Id", pids,
                      "Id,LastName,FirstName", label="persons"):
        persons[p["Id"]] = f"{p.get('LastName','')} {p.get('FirstName','')}".strip()
    print(f"persons resolved: {len(persons)}/{len(pids)}", file=sys.stderr)

    out = defaultdict(list)
    for r in rows:
        name = persons.get(r.get("PersonID"))
        if not name:
            continue
        out[str(r["BillID"])].append({
            "name": name,
            "initiator": bool(r.get("IsInitiator")),
            "from": (r.get("StartDate") or "")[:10] or None,
            "to": (r.get("EndDate") or "")[:10] or None,
            "reason": r.get("ReasonDesc") or None,
        })
    for v in out.values():
        v.sort(key=lambda x: (x["to"] or "", x["name"]))

    out_path = DATA / f"k{kn}_bill_history.json"
    out_path.write_text(json.dumps(dict(out), ensure_ascii=False), "utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size/1e6:.2f} MB, "
          f"{len(out)} bills, {sum(len(v) for v in out.values())} removed sponsors)")


if __name__ == "__main__":
    main()
