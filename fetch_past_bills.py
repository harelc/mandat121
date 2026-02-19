#!/usr/bin/env python3
"""
Fetch past Knesset bill votes from hasadna's public data mirror.

Only retains votes from MKs who also serve in Knesset 25, so the quiz
can use past data to increase coverage for cross-knesset MK matching.

Bills are saved to data/bills_past.json and tagged with their knesset
number so they can be easily identified and dropped.

Usage:
    python3 fetch_past_bills.py                  # fetch K23+K24
    python3 fetch_past_bills.py --knessets=23     # fetch only K23
    python3 fetch_past_bills.py --merge           # merge into bills.json
    python3 fetch_past_bills.py --drop            # remove all past bills from bills.json
"""

import csv
import io
import json
import re
import sys
import urllib.request
from pathlib import Path
from collections import Counter
from difflib import SequenceMatcher

DATA_DIR = Path(__file__).parent / "data"
BILLS_PATH = DATA_DIR / "bills.json"
PAST_PATH = DATA_DIR / "bills_past.json"

HASADNA_VOTE_HEADERS = "https://storage.googleapis.com/knesset-data-pipelines/data/votes/view_vote_rslts_hdr_approved/view_vote_rslts_hdr_approved.csv"
HASADNA_MK_VOTES = "https://storage.googleapis.com/knesset-data-pipelines/data/votes/vote_rslts_kmmbr_shadow/vote_rslts_kmmbr_shadow.csv"
HASADNA_BILLS_CSV = "https://storage.googleapis.com/knesset-data-pipelines/data/bills/kns_bill/kns_bill.csv"

BILL_TYPES = {53: "ממשלתית", 54: "פרטית", 55: "ועדה"}

NON_BILL_PATTERNS = [
    r'הצעת אי[\-\s]?אמון', r'אי[\-\s]?אמון', r'הודעת ראש הממשלה',
    r'הודעת הממשלה', r'בחירת\s', r'מינוי\s', r'הרכב\s+ועד',
    r'הצעה לסדר היום', r'הצעת סיכום', r'דיון\s+מיוחד', r'שאילתות',
    r'הצעת הוועדה המסדרת',
    r'להעביר את ה',
    r'^אישור החוק$',
    r'^שם החוק$',
    r'^הצבעה$',
    r'להחזיר את ה',
]
NON_BILL_RE = re.compile('|'.join(NON_BILL_PATTERNS))

DEFAULT_KNESSETS = [23, 24]

# ── CSV cache to avoid re-downloading ──
_csv_cache = {}

def download_csv(url, description):
    if url in _csv_cache:
        print(f"  Using cached {description}")
        return _csv_cache[url]
    print(f"  Downloading {description}...", flush=True)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(raw))
    rows = list(reader)
    print(f"    {len(rows)} rows")
    _csv_cache[url] = rows
    return rows


def get_k25_mk_names():
    if not BILLS_PATH.exists():
        return set()
    bills = json.loads(BILLS_PATH.read_text("utf-8"))
    names = set()
    for b in bills:
        if b.get("knesset") and b["knesset"] != 25:
            continue
        for mv in b.get("mk_votes", []):
            names.add(mv["mk_name"])
    return names


def name_parts(name):
    return frozenset(name.strip().replace("'", "").replace("`", "").replace("\u05f3", "").split())


def build_name_matcher(k25_names):
    return {name_parts(n): n for n in k25_names}


def is_bill_vote(desc):
    if not desc:
        return False
    if NON_BILL_RE.search(desc):
        return False
    if 'חוק' in desc:
        return True
    return False


def normalize_bill_name(name):
    name = re.sub(r',?\s*הת?שפ"?[א-ת].*$', '', name)
    name = re.sub(r'\(תיקון[^)]*\)', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def fetch_knesset_data(knesset_num, name_matcher, all_headers_rows, all_mk_vote_rows, catalog_rows):
    """Fetch bills for a single past knesset."""
    kn = str(knesset_num)
    print(f"\n--- Knesset {knesset_num} ---")

    # Filter vote headers for this knesset
    # sess_item_dscr has the actual bill name; vote_item_dscr has generic labels
    kn_headers = {}
    for r in all_headers_rows:
        if r.get("knesset_num") != kn:
            continue
        bill_name = (r.get("sess_item_dscr") or "").strip()
        vote_type = (r.get("vote_item_dscr") or "").strip()
        if not bill_name:
            continue
        if not is_bill_vote(bill_name):
            continue
        # Skip procedural vote types
        if vote_type in ("הסתייגות", "שם החוק", "הודעת הממשלה",
                         "הודעת יושב-ראש ועדת הכנסת", "הצעת הוועדה המסדרת"):
            continue
        if vote_type.startswith("להעביר") or vote_type.startswith("להחזיר") or vote_type.startswith("העברת"):
            continue
        kn_headers[r["id"]] = r
    print(f"  Bill-like votes: {len(kn_headers)}")

    if not kn_headers:
        return []

    # Collect per-MK votes for this knesset's bill votes, filtering to K25 MKs
    votes_by_id = {vid: [] for vid in kn_headers}
    matched_mks = set()

    for r in all_mk_vote_rows:
        vid = r.get("vote_id")
        if vid not in votes_by_id:
            continue
        k_name = (r.get("kmmbr_name") or "").strip()
        k25_name = name_matcher.get(name_parts(k_name))
        if k25_name:
            matched_mks.add(k25_name)
            result_str = r.get("vote_result", "")
            if result_str in ("1", "2", "3"):
                votes_by_id[vid].append({
                    "mk_name": k25_name,
                    "result": int(result_str),
                    "faction": (r.get("faction_name") or "").strip(),
                })

    print(f"  K25 MKs found: {len(matched_mks)}")

    # Build catalog for this knesset
    kn_catalog = []
    for r in catalog_rows:
        if r.get("KnessetNum") == kn:
            sub_type_id = int(r.get("SubTypeID", 0))
            kn_catalog.append({
                "bill_id": int(r["BillID"]),
                "name": r.get("Name", "").strip(),
                "bill_type": BILL_TYPES.get(sub_type_id, ""),
                "summary": (r.get("SummaryLaw") or "").strip(),
            })
    print(f"  Bill catalog entries: {len(kn_catalog)}")

    # Build bills
    bills = []
    for vid, header in kn_headers.items():
        mk_votes = votes_by_id.get(vid, [])
        if not mk_votes:
            continue

        desc = (header.get("sess_item_dscr") or "").strip()

        # Match to catalog
        bill_type = ""
        bill_id = None
        norm_desc = normalize_bill_name(desc)
        best_score = 0
        for cat in kn_catalog:
            norm_cat = normalize_bill_name(cat["name"])
            if not norm_cat:
                continue
            desc_words = set(norm_desc.split())
            cat_words = set(norm_cat.split())
            if not (desc_words & cat_words - {'הצעת', 'חוק', 'של', 'את', 'על', 'לא', 'להעביר'}):
                continue
            score = SequenceMatcher(None, norm_desc, norm_cat).ratio()
            if score > best_score and score > 0.4:
                best_score = score
                bill_type = cat["bill_type"]
                bill_id = cat["bill_id"]

        vote_type = (header.get("vote_item_dscr") or "").strip()
        # Map to reading stage
        if vote_type == "אישור החוק":
            reading = "קריאה שלישית"
        elif vote_type == "קריאה שנייה":
            reading = "קריאה שנייה"
        elif vote_type == "הצעת ועדה":
            reading = "הצעת ועדה"
        else:
            reading = vote_type

        bill = {
            "id": f"k{knesset_num}_{vid}",
            "name": desc,
            "vote_date": header.get("vote_date", ""),
            "vote_id": int(vid),
            "is_accepted": 1 if header.get("is_accepted") == "1" else 0,
            "total_for": int(header.get("total_for", 0)),
            "total_against": int(header.get("total_against", 0)),
            "total_abstain": int(header.get("total_abstain", 0)),
            "vote_description": reading,
            "mk_votes": mk_votes,
            "knesset": knesset_num,
            "bill_type": bill_type,
        }
        if bill_id:
            bill["bill_id"] = bill_id
        bills.append(bill)

    # Deduplicate: keep the highest reading stage per bill name
    READING_PRIORITY = {"קריאה שלישית": 3, "קריאה שנייה": 2, "הצעת ועדה": 1}
    by_name = {}
    for bill in bills:
        name = bill["name"]
        priority = READING_PRIORITY.get(bill.get("vote_description", ""), 0)
        if name not in by_name or priority > by_name[name][1]:
            by_name[name] = (bill, priority)

    before_dedup = len(bills)
    bills = [v[0] for v in by_name.values()]
    bills.sort(key=lambda b: b.get("vote_date", ""), reverse=True)

    type_counts = Counter(b.get("bill_type", "") for b in bills)
    reading_counts = Counter(b.get("vote_description", "") for b in bills)
    voter_counts = [len(b["mk_votes"]) for b in bills]
    print(f"  Before dedup: {before_dedup}, after: {len(bills)}")
    if voter_counts:
        print(f"  MK votes per bill: min={min(voter_counts)}, median={sorted(voter_counts)[len(voter_counts)//2]}, max={max(voter_counts)}")
    print(f"  By type: {dict(type_counts)}")
    print(f"  By reading: {dict(reading_counts)}")

    return bills


def main():
    if "--drop" in sys.argv:
        if not BILLS_PATH.exists():
            print("No bills.json found")
            return
        bills = json.loads(BILLS_PATH.read_text("utf-8"))
        before = len(bills)
        bills = [b for b in bills if not b.get("knesset") or b["knesset"] == 25]
        after = len(bills)
        BILLS_PATH.write_text(json.dumps(bills, ensure_ascii=False, indent=2), "utf-8")
        print(f"Dropped {before - after} past-knesset bills. {after} bills remain.")
        if PAST_PATH.exists():
            PAST_PATH.unlink()
            print(f"Removed {PAST_PATH}")
        return

    if "--merge" in sys.argv:
        if not PAST_PATH.exists():
            print("No past data found. Run without --merge first.")
            return
        past_bills = json.loads(PAST_PATH.read_text("utf-8"))
        bills = json.loads(BILLS_PATH.read_text("utf-8")) if BILLS_PATH.exists() else []
        # Remove existing past bills
        bills = [b for b in bills if not b.get("knesset") or b["knesset"] == 25]
        bills.extend(past_bills)
        bills.sort(key=lambda b: b.get("vote_date", ""), reverse=True)
        BILLS_PATH.write_text(json.dumps(bills, ensure_ascii=False, indent=2), "utf-8")
        k25 = sum(1 for b in bills if not b.get("knesset") or b["knesset"] == 25)
        print(f"Merged {len(past_bills)} past bills into bills.json (total: {len(bills)}, K25: {k25}, past: {len(past_bills)})")
        return

    # Parse --knessets flag
    knessets = DEFAULT_KNESSETS
    for arg in sys.argv[1:]:
        if arg.startswith("--knessets="):
            knessets = [int(k) for k in arg.split("=")[1].split(",")]

    print(f"=== Fetching past Knesset bills (K{',K'.join(str(k) for k in knessets)}) ===")

    k25_names = get_k25_mk_names()
    print(f"K25 MKs: {len(k25_names)}")
    name_matcher = build_name_matcher(k25_names)

    # Download shared data once
    all_headers = download_csv(HASADNA_VOTE_HEADERS, "vote headers")
    all_mk_votes = download_csv(HASADNA_MK_VOTES, "per-MK votes (~85MB)")
    catalog_rows = download_csv(HASADNA_BILLS_CSV, "bill catalog")

    all_bills = []
    for kn in knessets:
        bills = fetch_knesset_data(kn, name_matcher, all_headers, all_mk_votes, catalog_rows)
        all_bills.extend(bills)

    all_bills.sort(key=lambda b: b.get("vote_date", ""), reverse=True)

    print(f"\n=== Total ===")
    print(f"Past bills: {len(all_bills)}")
    by_knesset = Counter(b["knesset"] for b in all_bills)
    for k in sorted(by_knesset):
        print(f"  K{k}: {by_knesset[k]}")

    PAST_PATH.write_text(json.dumps(all_bills, ensure_ascii=False, indent=2), "utf-8")
    print(f"\nSaved to {PAST_PATH}")
    print(f"\nTo merge into bills.json:  python3 fetch_past_bills.py --merge")
    print(f"To drop past data:         python3 fetch_past_bills.py --drop")


if __name__ == "__main__":
    main()
