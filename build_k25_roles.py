#!/usr/bin/env python3
"""Government / Knesset / faction leadership roles for a Knesset.

The tenure builder (build_k25_tenure.py) reads only MK stints (PositionID
43/61), faction membership (54) and committee builders read committee
positions (41/42/66/67). Everything else a person held — שר, סגן שר,
ראש ממשלה, יו"ר הכנסת, יו"ר סיעה, ראש האופוזיציה… — lives in the same
KNS_PersonToPosition table under other PositionIDs, named by KNS_Position,
with the portfolio in DutyDesc/GovMinistryName. This harvests those.

Usage: python3 build_k25_roles.py [--knesset 25|26]  (default 25)
Output: data/k{N}_roles.json {"by_mk": {surname-first name: [role…]}}.
No roles published for N yet = exit 0, no write (safe daily-build no-op).
Name matching follows build_k25_tenure.py (surname-first UI names).
"""
import argparse, json, sys, urllib.parse, urllib.request
from pathlib import Path

DATA = Path(__file__).parent / "data"
V4 = "https://knesset.gov.il/OdataV4/ParliamentInfo/"
SVC = "https://knesset.gov.il/Odata/ParliamentInfo.svc/"  # V4 400s on KNS_Person; svc serves it
HDR = {"Accept": "application/json", "User-Agent": "biapia-research/1.0 (roles build)"}

# Leadership positions (KNS_Position), by family. MK/faction-membership and
# committee positions are deliberately excluded — other builders own them.
ROLE_POSITIONS = {
    45: "gov", 73: "gov", 31: "gov", 50: "gov", 51: "gov", 65: "gov",
    39: "gov", 57: "gov", 40: "gov", 59: "gov", 285079: "gov",
    122: "knesset", 123: "knesset", 285078: "knesset", 70: "knesset", 71: "knesset",
    48: "faction", 29: "faction", 30: "faction", 130: "faction", 131: "faction",
}


def get(base, entity, filt):
    rows, skip = [], 0
    while True:
        q = f"{entity}?" + urllib.parse.urlencode(
            {"$filter": filt, "$top": "100", "$skip": str(skip), "$format": "json"},
            quote_via=urllib.parse.quote)
        req = urllib.request.Request(base + q, headers=HDR)
        with urllib.request.urlopen(req, timeout=90) as r:
            vals = json.loads(r.read().decode()).get("value", [])
        rows += vals
        if len(vals) < 100:
            return rows
        skip += len(vals)


def wanted_names(kn):
    wanted = set()
    if kn == 25:
        wanted.update(json.load(open(DATA / "mk_photos.json", encoding="utf-8")))
        try:
            wanted.update(json.load(open(DATA / "mk_contacts.json", encoding="utf-8"))["by_name"])
        except Exception:
            pass
    else:
        try:
            prev = json.load(open(DATA / "k26_preview.json", encoding="utf-8"))
            for p in prev.get("parties", []):
                wanted.update(p.get("candidates", []))
        except Exception:
            pass
    try:
        wanted.update(json.load(open(DATA / f"k{kn}_vote_results.json", encoding="utf-8"))["names"])
    except Exception:
        pass
    return wanted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--knesset", type=int, default=25, choices=(25, 26))
    kn = ap.parse_args().knesset

    pos_desc = {p["Id"]: p["Description"]
                for p in get(V4, "KNS_Position", "Id gt 0")}
    ids = ",".join(str(i) for i in sorted(ROLE_POSITIONS))
    rows = get(V4, "KNS_PersonToPosition",
               f"KnessetNum eq {kn} and PositionID in ({ids})")
    if not rows:
        print(f"K{kn}: no leadership roles published yet — skipping roles build")
        return

    pids = sorted({r["PersonID"] for r in rows})
    persons = {}
    for i in range(0, len(pids), 20):
        filt = " or ".join(f"PersonID eq {p}" for p in pids[i:i + 20])
        for p in get(SVC, "KNS_Person", filt):
            persons[p["PersonID"]] = p
    print(f"role rows: {len(rows)}, persons: {len(persons)}", file=sys.stderr)

    WANTED_ALIAS = {"גנץ בני": "גנץ בנימין", "סגלוביץ יואב": "סגלוביץ' יואב",
                    "מלקו צגה צגנש": "מלקו צגה", "סטרוק אורית": "סטרוק אורית מלכה",
                    "פינדרוס יצחק זאב": "פינדרוס יצחק"}

    def norm_name(s):
        return " ".join(str(s).replace("'", "").replace("׳", "").split())

    wanted = {WANTED_ALIAS.get(w, w) for w in wanted_names(kn)}
    orig_by_norm = {norm_name(w): w for w in wanted}
    wanted = set(orig_by_norm)

    def resolve(pid):
        p = persons.get(pid)
        if not p:
            return None
        base = norm_name(f"{p['LastName']} {p['FirstName']}")
        key = base if base in wanted else next(
            (w for w in wanted if w == base or w.startswith(base + " ") or base.startswith(w + " ")), None)
        return orig_by_norm[key] if key else None

    by_mk, unmatched = {}, set()
    for r in rows:
        key = resolve(r["PersonID"])
        if not key:
            p = persons.get(r["PersonID"])
            if p:
                unmatched.add(norm_name(f"{p['LastName']} {p['FirstName']}"))
            continue
        by_mk.setdefault(key, []).append({
            "role": r.get("DutyDesc") or pos_desc.get(r["PositionID"], ""),
            "kind": ROLE_POSITIONS[r["PositionID"]],
            "ministry": r.get("GovMinistryName") or None,
            "faction": r.get("FactionName") or None,
            "gov": r.get("GovernmentNum"),
            "from": (r.get("StartDate") or "")[:10],
            "to": (r.get("FinishDate") or None) and r["FinishDate"][:10],
        })
    for v in by_mk.values():
        seen, uniq = set(), []
        for r in v:  # the feed carries occasional duplicate stint rows
            k = (r["role"], r["ministry"], r["faction"], r["from"], r["to"])
            if k not in seen:
                seen.add(k)
                uniq.append(r)
        v[:] = uniq
        v.sort(key=lambda x: (x["to"] is not None, -(int((x["from"] or "0").replace("-", "") or 0))))
    json.dump({"_note": f"Leadership/government roles from KNS_PersonToPosition KnessetNum={kn} "
                        f"(PositionIDs {sorted(ROLE_POSITIONS)}, named via KNS_Position/DutyDesc), fetched at build time.",
               "by_mk": by_mk},
              open(DATA / f"k{kn}_roles.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"MKs with roles: {len(by_mk)}, unmatched persons: {sorted(unmatched)}")


if __name__ == "__main__":
    main()
