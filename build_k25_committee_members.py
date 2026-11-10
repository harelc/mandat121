#!/usr/bin/env python3
"""Committee memberships for a Knesset, from official OData.

KNS_PersonToPosition with CommitteeID set: PositionID 41 = committee chair
(יו"ר ועדה), 42/66 = member (חבר/חברת ועדה), 67 = substitute member.
Person matching follows build_k25_tenure.py (LastName+FirstName against the
surname-first names our UI uses).

Usage: python3 build_k25_committee_members.py [--knesset 25|26] (default 25)
Output: data/k{N}_committee_members.json. No committee positions published
for N yet = exit 0, no write (safe no-op in the daily K26 step).
  {"by_mk": {name: [{committee_id, committee, role, from, to}]},
   "by_committee": {committee_id: [{name, role, from, to}]}}
"""
import argparse, json, sys, urllib.parse, urllib.request
from pathlib import Path

DATA = Path(__file__).parent / "data"

V4 = "https://knesset.gov.il/OdataV4/ParliamentInfo/"   # positions live here
SVC = "https://knesset.gov.il/Odata/ParliamentInfo.svc/"  # V4 400s on KNS_Person; svc serves it
HDR = {"Accept": "application/json", "User-Agent": "biapia-research/1.0 (committee-members build)"}
ROLES = {41: "chair", 42: "member", 66: "member", 67: "substitute"}

# K26 CEC candidate-list spelling -> the spelling OData (KNS_Person) and
# our K25 datasets use. Mirrors NAME_CANON in index.html and
# K26_NAME_ALIAS in build_k25_tenure.py — keep in sync.
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--knesset", type=int, default=25, choices=(25, 26))
    kn = ap.parse_args().knesset
    pos = [r for r in get("KNS_PersonToPosition", f"KnessetNum eq {kn}")
           if r.get("PositionID") in ROLES and r.get("CommitteeID")]
    if not pos:
        print(f"K{kn}: no committee positions published yet — skipping committee-members build")
        return
    pids = sorted({r["PersonID"] for r in pos})
    persons = {}
    for i in range(0, len(pids), 20):
        filt = " or ".join(f"PersonID eq {p}" for p in pids[i:i + 20])
        for p in get("KNS_Person", filt, base=SVC):
            persons[p["PersonID"]] = p
    print(f"committee positions: {len(pos)}, persons: {len(persons)}", file=sys.stderr)

    wanted = set()
    if kn == 25:
        wanted.update(json.load(open(DATA / "mk_photos.json", encoding="utf-8")))
    else:
        try:  # CEC preview lists in full, as in build_k25_tenure.wanted_names:
            # Norwegian-law replacements were not in the elected first-N.
            prev = json.load(open(DATA / "k26_preview.json", encoding="utf-8"))
            for p in prev.get("parties", []):
                wanted.update(p.get("candidates", []))
            wanted.update(K26_NAME_ALIAS[n] for n in list(wanted)
                          if n in K26_NAME_ALIAS)
        except Exception:
            pass
    try:  # mandate121-native: every name in the per-MK vote harvest
        wanted.update(json.load(open(DATA / f"k{kn}_vote_results.json", encoding="utf-8"))["names"])
    except Exception:
        pass
    if not wanted:
        print(f"K{kn}: no name pool to match persons against — skipping")
        return
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
              open(DATA / f"k{kn}_committee_members.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(f"MKs with committees: {len(by_mk)}, committees: {len(by_cmt)}, unmatched persons: {len(set(unmatched))}")
    print("sample:", list(by_mk.items())[:1])

if __name__ == "__main__":
    main()
