#!/usr/bin/env python3
"""Authoritative MK tenure periods for a Knesset, from official OData.

KNS_PersonToPosition (KnessetNum=N, PositionID=43 = חבר כנסת) gives real
StartDate/FinishDate per stint — this handles Norwegian-law resignations and
mid-term replacements exactly, unlike first/last-vote approximations.

Usage: python3 build_k25_tenure.py [--knesset 25|26]   (default 25)
Output: data/k{N}_tenure.json. If OData has no MK positions for N yet,
exits 0 without writing (so the daily K26 step is a safe no-op until the
feed publishes that layer).
  { "<surname-first name as in bills.json mk_votes>": [[from, to|null], ...] }
Names are matched by LastName+FirstName against the names our datasets use
(mk_photos.json keys + mk_votes names); unmatched persons are reported.
For K26 the name pool is the CEC preview roster (data/k26_preview.json
elected) plus any harvested K26 vote-result names.
"""
import argparse, json, sys, urllib.parse, urllib.request
from pathlib import Path

BASE = "https://knesset.gov.il/Odata/ParliamentInfo.svc/"
DATA = Path(__file__).parent / "data"

# K26 CEC candidate-list spelling -> the spelling OData (KNS_Person) and
# our K25 datasets use. Mirrors NAME_CANON in index.html — keep in sync.
# Without these, 19 of the 120 serving K26 MKs (ביסמוט בועז, איזנקוט גדי…)
# match no preview candidate and drop out of the serving roster entirely.
K26_NAME_ALIAS = {
    "סון הר-מלך לימור": "סון הר מלך לימור", "אל הואשלה ואליד": "אלהואשלה ואליד",
    "ביסמוט בעז": "ביסמוט בועז", "איזנקוט גד": "איזנקוט גדי",
    "כהן אליהו": "כהן אלי", "לוין יריב גדעון": "לוין יריב",
    "רגב מרים": "רגב מירי מרים", "סער גדעון משה": "סער גדעון",
    "זוהר מכלוף": "זוהר מכלוף מיקי", "עטייה חוה": "עטייה חוה אתי",
    "גמליאל דמרי גילה": "גמליאל גילה", "טרופר יחיאל משה": "טרופר חילי",
    "גולדקנופ יצחק יששכר": "גולדקנופ יצחק", "מלינובסקי קונין יוליה": "מלינובסקי יוליה",
    "גוטליב רויטל טלי": "גוטליב טלי", "מלכיאלי מיכאל משה": "מלכיאלי מיכאל",
    "סמוטריץ בצלאל יואל": "סמוטריץ' בצלאל", "רוטמן שמחה דן": "רוטמן שמחה",
    "מושיאשוילי סימיון": "מושיאשוילי סימון",
    "טרנר אייל קרן": "טרנר קרן", "הירש נגרי מיכל טליה": "נגרי מיכל",
    "פינק יאיר": "פינק יאיא (יאיר)", "לסקי שוץ גבריאלה": "לסקי גבי",
    "גואילי טלי": "גואילי טליק", "שטארק אלי": "שטארק אליקים",
    "הרוש גיטי עינבר": "הרוש גיטי ענבר", "יחזקאלי בליליוס ענבר": "יחזקאלי ענבר",
    "מסרי עבד אל כרים": "מסרי עבד אלכרים",
    "איפרגן תאיר סולי": "איפרגן תאיר", "פולקמן רועי שמואל": "פולקמן רועי",
    "תיבון נעם משה": "תיבון נעם", "רוזנטל משה עזריאל": "רוזנטל משה",
    "וויספיש יהודה לייב": "וויספיש יהודה", "דורפמן חנמאל שיראי": "דורפמן חנמאל",
    "בוסו אוריאל מנחם": "בוסו אוריאל", "פייגלין משה זלמן": "פייגלין משה",
    "איתם איתמר מרדכי": "איתם איתמר",
}

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

def wanted_names(kn):
    """Surname-first names the UI/datasets use for this Knesset."""
    wanted = set()
    if kn == 25:
        wanted.update(json.load(open(DATA / "mk_photos.json", encoding="utf-8")))
        try:
            wanted.update(json.load(open(DATA / "mk_contacts.json", encoding="utf-8"))["by_name"])
        except Exception:
            pass
    else:
        try:  # CEC preview lists, in full: a Norwegian-law replacement is a
            # candidate who was NOT in the elected first-N, so matching only
            # `elected` would miss exactly the people who start serving later.
            prev = json.load(open(DATA / "k26_preview.json", encoding="utf-8"))
            for p in prev.get("parties", []):
                wanted.update(p.get("candidates", []))
            # OData spells these MKs differently than the CEC lists do;
            # offer the canonical spelling too so resolve() can match it.
            wanted.update(K26_NAME_ALIAS[n] for n in list(wanted)
                          if n in K26_NAME_ALIAS)
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
    # PositionID 43 = חבר הכנסת, 61 = חברת הכנסת (gendered position titles)
    pos = get("KNS_PersonToPosition", f"KnessetNum eq {kn} and (PositionID eq 43 or PositionID eq 61)")
    if not pos:
        print(f"K{kn}: no MK positions published yet — skipping tenure build")
        return
    pids = sorted({r["PersonID"] for r in pos})
    persons = {}
    for i in range(0, len(pids), 20):  # or-chain batches (no `in` operator assumed)
        chunk = pids[i:i + 20]
        filt = " or ".join(f"PersonID eq {p}" for p in chunk)
        for p in get("KNS_Person", filt):
            persons[p["PersonID"]] = p
    print(f"positions: {len(pos)} rows, persons: {len(persons)}", file=sys.stderr)

    # Names our UI uses, surname-first (see wanted_names). For K25 the
    # current-120 roster (mk_contacts) is authoritative for who serves now:
    # a newly sworn-in MK has no votes and no photo-snapshot entry yet, and
    # without this they landed in the UI with no faction ("ללא סיעה").
    wanted = wanted_names(kn)

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
    fpos = get("KNS_PersonToPosition", f"KnessetNum eq {kn} and PositionID eq 54")
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

    json.dump({"_note": f"MK tenure stints [from,to] (to=null = still serving) from KNS_PersonToPosition KnessetNum={kn} PositionID=43/61; factions + faction_stints [from,to,faction] from PositionID=54, fetched at build time.",
               "tenure": {k: sorted(v) for k, v in tenure.items()}, "factions": factions,
               "faction_stints": faction_stints},
              open(DATA / f"k{kn}_tenure.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    missing = sorted(set(orig_by_norm.values()) - set(tenure))
    print(f"MATCHED {len(tenure)}/{len(wanted)} UI names; persons unmatched: {len(unmatched_persons)}")
    # K26's pool is every candidate (466) so Norwegian-law replacements can
    # resolve; most will never serve, so listing all of them is just noise.
    print("UI names without tenure:", missing if kn == 25 else f"{len(missing)} candidates never (yet) in a tenure stint")
    # Every UI name must resolve to a faction — the UI renders a missing one
    # as "ללא סיעה", which is never true for a serving/former K25 MK.
    no_faction = sorted(set(orig_by_norm.values()) - set(factions))
    print("UI names without faction:", no_faction if kn == 25 else f"{len(no_faction)} (candidates, expected)")
    print("persons without UI name:", unmatched_persons[:20])

if __name__ == "__main__":
    main()
