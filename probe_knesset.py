#!/usr/bin/env python3
"""
Knesset OData v4 readiness probe.

Answers two questions, read-only, using only the official OData v4 feed:
  1. Has a given Knesset (default: 26) started publishing data yet?
  2. What is the freshest activity in the current Knesset?

Usage:
    python3 probe_knesset.py            # probe Knesset 26 + latest K25 activity
    python3 probe_knesset.py --knesset 26
    python3 probe_knesset.py --json     # machine-readable output

Exit code: 0 if the probed Knesset has data, 2 if not yet, 1 on API error.
Stdlib only. No auth, no secrets.
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request

ODATA_BASE = "https://knesset.gov.il/OdataV4/ParliamentInfo"
HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 biapia-probe"}


def odata(entity, params):
    url = f"{ODATA_BASE}/{entity}"
    if params:
        url += "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def count(entity, filt):
    data = odata(entity, {"$filter": filt, "$top": "0", "$count": "true"})
    return data.get("@odata.count", 0)


def latest(entity, filt, orderby, select, top=1):
    data = odata(entity, {
        "$filter": filt, "$top": str(top),
        "$orderby": orderby, "$select": select,
    })
    return data.get("value", [])


def probe(knesset_num):
    kn_filter = f"KnessetNum eq {knesset_num}"
    report = {"knesset": knesset_num, "ready": False, "entities": {}}
    checks = {
        "bills": ("KNS_Bill", kn_filter),
        "mks (PositionID=54)": (
            "KNS_PersonToPosition",
            f"PositionID eq 54 and {kn_filter}",
        ),
        "factions": ("KNS_Faction", kn_filter),
        "committees": ("KNS_Committee", kn_filter),
        "plenum_sessions": ("KNS_PlenumSession", kn_filter),
        "committee_sessions": ("KNS_CommitteeSession", kn_filter),
        "is_current_knesset": (
            "KNS_KnessetDates", f"{kn_filter} and IsCurrent eq true"
        ),
    }
    for label, (entity, filt) in checks.items():
        report["entities"][label] = count(entity, filt)
    report["ready"] = report["entities"]["bills"] > 0
    return report


def latest_activity():
    out = {}
    current = odata("KNS_KnessetDates", {"$filter": "IsCurrent eq true"})
    rows = current.get("value", [])
    out["current_knesset"] = rows[0]["KnessetNum"] if rows else None

    votes = latest(
        "KNS_PlenumVote", "VoteDateTime gt 2020-01-01T00:00:00Z",
        "VoteDateTime desc", "Id,VoteDateTime,ItemID,ForOptionDesc",
    )
    out["latest_plenum_vote"] = votes[0] if votes else None

    sessions = latest(
        "KNS_CommitteeSession", "KnessetNum gt 0",
        "StartDate desc", "Id,KnessetNum,CommitteeID,StartDate",
    )
    out["latest_committee_session"] = sessions[0] if sessions else None

    bills = latest(
        "KNS_Bill", "KnessetNum gt 0",
        "LastUpdatedDate desc", "Id,KnessetNum,Name,StatusID,LastUpdatedDate",
    )
    out["latest_updated_bill"] = bills[0] if bills else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--knesset", type=int, default=26)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        report = probe(args.knesset)
        activity = latest_activity()
    except Exception as e:  # network / API failure — report, don't fabricate
        print(f"ERROR probing OData feed: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"probe": report, "activity": activity},
                         ensure_ascii=False, indent=2))
    else:
        print(f"Knesset {args.knesset} readiness:")
        for label, n in report["entities"].items():
            print(f"  {label:<24} {n}")
        print(f"  => {'READY' if report['ready'] else 'NOT YET PUBLISHED'}")
        print("\nLatest activity in feed:")
        print(f"  current Knesset (KNS_KnessetDates.IsCurrent): "
              f"{activity['current_knesset']}")
        v = activity["latest_plenum_vote"]
        if v:
            print(f"  latest plenum vote: {v['VoteDateTime'][:10]} "
                  f"vote {v['Id']} — {(v.get('ForOptionDesc') or '')[:60]}")
        c = activity["latest_committee_session"]
        if c:
            print(f"  latest committee session: {c['StartDate'][:10]} "
                  f"(K{c['KnessetNum']}, committee {c['CommitteeID']})")
        b = activity["latest_updated_bill"]
        if b:
            print(f"  latest updated bill: {b['LastUpdatedDate'][:10]} "
                  f"K{b['KnessetNum']} status {b['StatusID']} — "
                  f"{(b.get('Name') or '')[:60]}")

    return 0 if report["ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
