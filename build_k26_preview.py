#!/usr/bin/env python3
"""
Snapshot the incoming Knesset-26 roster from ../elections-vote-transfer.

Source (harvested there from Central Elections Committee candidate lists,
2026-10-23/29): site/data/k26_mks.json — party -> candidates in official
list order (surname-first). Seats per party: site/data/wiki_official_results.json
key "26" (official CEC count). Elected = first `seats` candidates in list order.

Output: data/k26_preview.json — consumed by index.html (המנדט ה־121 app). Re-run when the
sibling repo updates. This is a preview only: the authoritative roster is
KNS_PersonToPosition (KnessetNum=26) once the OData feed publishes it —
check with probe_knesset.py.
"""
import json
from pathlib import Path

SRC = Path(__file__).parent.parent / "elections-vote-transfer" / "site" / "data"
OUT = Path(__file__).parent / "data" / "k26_preview.json"

# Colors matched to elections-vote-transfer/party_config.py where applicable.
PARTY_COLORS = {
    "הליכוד": "#2563eb",
    "ישר": "#0891b2",
    "ביחד": "#f97316",
    "הרשימה המשותפת": "#0d9488",
    "הדמוקרטים": "#dc2626",
    "רע״ם": "#84cc16",
    "רע\"ם": "#84cc16",
    "ישראל ביתנו": "#db2777",
    "עוצמה יהודית": "#b91c1c",
    "יהדות התורה": "#4b5563",
    "ש״ס": "#1e3a8a",
    "ש\"ס": "#1e3a8a",
    "הציונות הדתית": "#92400e",
}


def main():
    mks = json.loads((SRC / "k26_mks.json").read_text("utf-8"))
    results = json.loads((SRC / "wiki_official_results.json").read_text("utf-8"))
    seats = {p["name"]: p["seats"] for p in results["26"]}

    def seats_for(party):
        if party in seats:
            return seats[party]
        # quote-style variants (״ vs ")
        for name, n in seats.items():
            if name.replace("״", '"') == party.replace("״", '"'):
                return n
        return 0

    parties = []
    for party, candidates in mks["parties"].items():
        n = seats_for(party)
        parties.append({
            "name": party,
            "seats": n,
            "color": PARTY_COLORS.get(party, "#64748b"),
            "candidates": candidates,
            "elected": candidates[:n],
        })
    parties.sort(key=lambda p: -p["seats"])

    total = sum(p["seats"] for p in parties)
    assert total == 120, f"seat total is {total}, expected 120"

    OUT.write_text(json.dumps({
        "note": mks.get("_note", ""),
        "source": "Central Elections Committee lists via elections-vote-transfer",
        "parties": parties,
    }, ensure_ascii=False, indent=1), "utf-8")
    print(f"Wrote {OUT}: {len(parties)} parties, {total} seats, "
          f"{sum(len(p['candidates']) for p in parties)} candidates")


if __name__ == "__main__":
    main()
