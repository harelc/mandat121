#!/usr/bin/env python3
"""Committee memberships for Knesset 25, from official OData.

KNS_PersonToPosition with CommitteeID set: PositionID 41 = committee chair
(יו"ר ועדה), 42/66 = member (חבר/חברת ועדה), 67 = substitute member.
Person matching follows build_k25_tenure.py (LastName+FirstName against the
surname-first names our UI uses).

Output: data/k25_committee_members.json
  {"by_mk": {name: [{committee_id, committee, role, from, to}]},
   "by_committee": {committee_id: [{name, role, from, to}]}}
"""
import json, sys, urllib.parse, urllib.request

V4 = "https://knesset.gov.il/OdataV4/ParliamentInfo/"   # positions live here
SVC = "https://knesset.gov.il/Odata/ParliamentInfo.svc/"  # V4 400s on KNS_Person; svc serves it
HDR = {"Accept": "application/json", "User-Agent": "biapia-research/1.0 (committee-members build)"}
ROLES = {41: "chair", 42: "member", 66: "member", 67: "substitute"}

def get(entity, filt, base=None):
    rows, skip = [], 0
    while True:
        q = f"{entity}?" + urllib.parse.urlencode(
            {"$filter": filt, "$top": "100", "$skip": str(skip), "$format": "json"}, quote_via=urllib.parse.quote)
        req = urllib.request.Request((base or V4) + q, headers=HDR)
        with urllib.request.urlopen(req, timeout=90) as r:
            vals = json.loads(r.read().decode()).get("value", [])
        rows += vals
        if len(vals) < 100:
            return rows
        skip += len(vals)

def norm(s):
    return " ".join(str(s).replace("'", "").replace("׳", "").split())

def main():
    pos = [r for r in get("KNS_PersonToPosition", "KnessetNum eq 25")
           if r.get("PositionID") in ROLES and r.get("CommitteeID")]
    pids = sorted({r["PersonID"] for r in pos})
    persons = {}
    for i in range(0, len(pids), 20):
        filt = " or ".join(f"PersonID eq {p}" for p in pids[i:i + 20])
        for p in get("KNS_Person", filt, base=SVC):
            persons[p["PersonID"]] = p
    print(f"committee positions: {len(pos)}, persons: {len(persons)}", file=sys.stderr)

    wanted = set(json.load(open("data/mk_photos.json", encoding="utf-8")))
    try:  # mandate121-native: every name in the per-MK vote harvest
        wanted.update(json.load(open("data/k25_vote_results.json", encoding="utf-8"))["names"])
    except Exception:
        pass
    orig = {norm(w): w for w in wanted}
    wanted_n = set(orig)
    ALIAS = {"גנץ בנימין": "גנץ בני"}

    by_mk, by_cmt, unmatched = {}, {}, []
    for r in pos:
        p = persons.get(r["PersonID"])
        if not p:
            continue
        base = ALIAS.get(norm(f"{p['LastName']} {p['FirstName']}"), norm(f"{p['LastName']} {p['FirstName']}"))
        key = base if base in wanted_n else next(
            (w for w in wanted_n if w.startswith(base + " ") or base.startswith(w + " ")), None)
        if not key:
            unmatched.append(base)
            continue
        name = orig[key]
        rec = {"committee_id": r["CommitteeID"], "committee": r.get("CommitteeName") or "",
               "role": ROLES[r["PositionID"]],
               "from": (r.get("StartDate") or "")[:10], "to": (r.get("FinishDate") or "")[:10] or None}
        by_mk.setdefault(name, []).append(rec)
        by_cmt.setdefault(str(r["CommitteeID"]), []).append({"name": name, **rec})
    for v in by_mk.values():
        v.sort(key=lambda x: (x["to"] is not None, x["from"]))
    json.dump({"by_mk": by_mk, "by_committee": by_cmt},
              open("data/k25_committee_members.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(f"MKs with committees: {len(by_mk)}, committees: {len(by_cmt)}, unmatched persons: {len(set(unmatched))}")
    print("sample:", list(by_mk.items())[:1])

if __name__ == "__main__":
    main()
