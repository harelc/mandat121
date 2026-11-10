#!/usr/bin/env python3
"""Authoritative MK tenure periods for Knesset 25, from official OData.

KNS_PersonToPosition (KnessetNum=25, PositionID=43 = חבר כנסת) gives real
StartDate/FinishDate per stint — this handles Norwegian-law resignations and
mid-term replacements exactly, unlike first/last-vote approximations.

Output: data/k25_tenure.json
  { "<surname-first name as in bills.json mk_votes>": [[from, to|null], ...] }
Names are matched by LastName+FirstName against the names our datasets use
(mk_photos.json keys + mk_votes names); unmatched persons are reported.
"""
import json, sys, urllib.request

BASE = "https://knesset.gov.il/Odata/ParliamentInfo.svc/"

def get(entity, filt):
    rows, skip = [], 0
    while True:  # service caps pages at 100 rows regardless of $top
        q = f"{entity}?$filter={urllib.parse.quote(filt)}&$top=100&$skip={skip}&$format=json"
        req = urllib.request.Request(BASE + q, headers={"Accept": "application/json", "User-Agent": "biapia-research/1.0"})
        with urllib.request.urlopen(req, timeout=90) as r:
            vals = json.load(r).get("value", [])
        rows += vals
        if len(vals) < 100:
            return rows
        skip += len(vals)

def main():
    # PositionID 43 = חבר הכנסת, 61 = חברת הכנסת (gendered position titles)
    pos = get("KNS_PersonToPosition", "KnessetNum eq 25 and (PositionID eq 43 or PositionID eq 61)")
    pids = sorted({r["PersonID"] for r in pos})
    persons = {}
    for i in range(0, len(pids), 20):  # or-chain batches (no `in` operator assumed)
        chunk = pids[i:i + 20]
        filt = " or ".join(f"PersonID eq {p}" for p in chunk)
        for p in get("KNS_Person", filt):
            persons[p["PersonID"]] = p
    print(f"positions: {len(pos)} rows, persons: {len(persons)}", file=sys.stderr)

    # Names our UI uses, surname-first.
    # mandate121-native name sources (no legacy game dataset): the roster
    # file + every name in the per-MK vote harvest.
    wanted = set(json.load(open("data/mk_photos.json", encoding="utf-8")))
    try:
        wanted.update(json.load(open("data/k25_vote_results.json", encoding="utf-8"))["names"])
    except Exception:
        pass
    # The current-120 roster (mk_contacts) is authoritative for who serves
    # now: a newly sworn-in MK has no votes and no photo-snapshot entry yet,
    # and without this they landed in the UI with no faction ("ללא סיעה").
    try:
        wanted.update(json.load(open("data/mk_contacts.json", encoding="utf-8"))["by_name"])
    except Exception:
        pass

    def norm_name(s):
        return " ".join(str(s).replace("'", "").replace("׳", "").split())

    # Canonical name = the vote-harvest spelling; roster/photo spellings of
    # the same person collapse onto it (mirrors NAME_CANON in index.html —
    # keep the two in sync), else one person splits into two UI entries.
    WANTED_ALIAS = {"גנץ בני": "גנץ בנימין", "סגלוביץ יואב": "סגלוביץ' יואב",
                    "מלקו צגה צגנש": "מלקו צגה", "סטרוק אורית": "סטרוק אורית מלכה",
                    "פינדרוס יצחק זאב": "פינדרוס יצחק"}
    wanted = {WANTED_ALIAS.get(w, w) for w in wanted}
    orig_by_norm = {norm_name(w): w for w in wanted}
    wanted = set(orig_by_norm)
    NAME_ALIAS = {}  # canonical name = the vote-harvest spelling ("גנץ בנימין"); the UI's normName maps the roster's "גנץ בני" onto it

    def resolve(pid):
        p = persons.get(pid)
        if not p:
            return None
        base = norm_name(f"{p['LastName']} {p['FirstName']}")
        base = NAME_ALIAS.get(base, base)
        # exact match, else surname + first-token-of-given-name prefix match
        key = base if base in wanted else next(
            (w for w in wanted if w == base or w.startswith(base + " ") or base.startswith(w + " ")), None)
        return orig_by_norm[key] if key else None

    tenure, unmatched_persons = {}, []
    stints = {}
    for r in pos:
        stints.setdefault(r["PersonID"], []).append(
            [(r["StartDate"] or "")[:10], (r["FinishDate"] or None) and r["FinishDate"][:10]])
    for pid, periods in stints.items():
        key = resolve(pid)
        if key:
            tenure.setdefault(key, []).extend(periods)
        elif persons.get(pid):
            p = persons[pid]
            unmatched_persons.append(norm_name(f"{p['LastName']} {p['FirstName']}"))

    # Faction membership (PositionID 54 = חבר/ת סיעה, carries FactionName +
    # Start/FinishDate per stint): emit the full dated stints so the UI can
    # answer "which faction was this MK in on the day of that vote" —
    # splits and faction switches are real in K25. `factions` keeps the
    # latest-stint convenience map.
    factions = {}
    faction_stints = {}
    fpos = get("KNS_PersonToPosition", "KnessetNum eq 25 and PositionID eq 54")
    latest = {}
    for r in fpos:
        if not r.get("FactionName"):
            continue
        cur = latest.get(r["PersonID"])
        if not cur or (r["StartDate"] or "") >= (cur["StartDate"] or ""):
            latest[r["PersonID"]] = r
    for pid, r in latest.items():
        key = resolve(pid)
        if key:
            factions[key] = r["FactionName"]
    for r in fpos:
        if not r.get("FactionName"):
            continue
        key = resolve(r["PersonID"])
        if key:
            faction_stints.setdefault(key, []).append(
                [(r["StartDate"] or "")[:10], (r["FinishDate"] or None) and r["FinishDate"][:10], r["FactionName"]])
    for v in faction_stints.values():
        v.sort(key=lambda s: s[0] or "")
    print(f"faction rows: {len(fpos)}, mapped: {len(factions)}", file=sys.stderr)

    json.dump({"_note": "MK tenure stints [from,to] (to=null = still serving) from KNS_PersonToPosition KnessetNum=25 PositionID=43/61; factions + faction_stints [from,to,faction] from PositionID=54, fetched at build time.",
               "tenure": {k: sorted(v) for k, v in tenure.items()}, "factions": factions,
               "faction_stints": faction_stints},
              open("data/k25_tenure.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    missing = sorted(set(orig_by_norm.values()) - set(tenure))
    print(f"MATCHED {len(tenure)}/{len(wanted)} UI names; persons unmatched: {len(unmatched_persons)}")
    print("UI names without tenure:", missing)
    # Every UI name must resolve to a faction — the UI renders a missing one
    # as "ללא סיעה", which is never true for a serving/former K25 MK.
    no_faction = sorted(set(orig_by_norm.values()) - set(factions))
    print("UI names without faction:", no_faction)
    print("persons without UI name:", unmatched_persons[:20])

if __name__ == "__main__":
    main()
