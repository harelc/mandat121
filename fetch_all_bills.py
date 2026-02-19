#!/usr/bin/env python3
"""
Fetch ALL Knesset 25 bills — government, private, and committee.

Strategy:
  1. Download the KNS_Bill catalog from hasadna's public mirror (GCS CSV)
     to get the full list of bills with type (government/private/committee).
  2. Try the Knesset OData API for bill documents & summaries.
  3. Crawl the Knesset Votes REST API for per-MK vote breakdowns.
  4. Match vote records to OData bills by name similarity.
  5. Merge into bills.json preserving existing data.

Usage:
    python3 fetch_all_bills.py                  # full run
    python3 fetch_all_bills.py --votes-only     # skip catalog, just crawl more votes
    python3 fetch_all_bills.py --catalog-only   # just refresh the bill catalog
"""

import csv
import io
import json
import re
import time
import sys
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

BILLS_PATH = DATA_DIR / "bills.json"
CATALOG_PATH = DATA_DIR / "bill_catalog.json"

# APIs
VOTES_API = "https://knesset.gov.il/WebSiteApi/knessetapi/Votes/GetVoteDetails"
PARLIAMENT_BASE = "https://knesset.gov.il/Odata/ParliamentInfo.svc"
HASADNA_BILLS_CSV = "https://storage.googleapis.com/knesset-data-pipelines/data/bills/kns_bill/kns_bill.csv"

TARGET_KNESSET = 25
START_VOTE_ID = 46000  # scan wider range
MIN_VOTE_ID = 38000

# Bill type IDs from OData
BILL_TYPES = {53: "ממשלתית", 54: "פרטית", 55: "ועדה"}

# Patterns for non-bill votes (procedural, no-confidence, etc.)
NON_BILL_PATTERNS = [
    r'הצעת אי[\-\s]?אמון', r'אי[\-\s]?אמון', r'הודעת ראש הממשלה',
    r'הודעת הממשלה', r'בחירת\s', r'מינוי\s', r'הרכב\s+ועד',
    r'הצעה לסדר היום', r'הצעת סיכום', r'דיון\s+מיוחד', r'שאילתות',
    r'להעביר את ה',
]
NON_BILL_RE = re.compile('|'.join(NON_BILL_PATTERNS))

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://knesset.gov.il/",
}


def is_bill_vote(name):
    """Check if a vote name looks like a bill (broadly)."""
    if not name:
        return False
    if NON_BILL_RE.search(name):
        return False
    # Accept anything with חוק, ועדה, המלצ, or just not excluded
    if 'חוק' in name or 'ועדה' in name or 'המלצ' in name:
        return True
    # Also accept government bill patterns
    if 'הממשלה' in name or 'ממשלתית' in name:
        return True
    # Accept anything else that's not in exclusion list — broader net
    return True


# ── Step 1: Download bill catalog from hasadna GCS ──

def fetch_bill_catalog():
    """Download KNS_Bill CSV from hasadna public mirror, filter to K25."""
    print("\n=== Step 1: Downloading bill catalog from hasadna ===")
    req = urllib.request.Request(HASADNA_BILLS_CSV)
    req.add_header("User-Agent", "Mozilla/5.0")

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as e:
        print(f"  ERROR downloading catalog: {e}")
        return []

    reader = csv.DictReader(io.StringIO(raw))
    all_rows = list(reader)
    print(f"  Total bills in CSV: {len(all_rows)}")

    k25 = []
    for r in all_rows:
        if r.get("KnessetNum") != str(TARGET_KNESSET):
            continue
        sub_type_id = int(r.get("SubTypeID", 0))
        k25.append({
            "bill_id": int(r["BillID"]),
            "name": r.get("Name", "").strip(),
            "sub_type_id": sub_type_id,
            "bill_type": BILL_TYPES.get(sub_type_id, ""),
            "status_id": int(r.get("StatusID", 0)),
            "summary": (r.get("SummaryLaw") or "").strip(),
            "private_number": r.get("PrivateNumber", ""),
        })

    from collections import Counter
    type_counts = Counter(b["bill_type"] for b in k25)
    print(f"  K25 bills: {len(k25)}")
    for t, c in type_counts.most_common():
        print(f"    {t or 'unknown'}: {c}")

    # Save catalog
    CATALOG_PATH.write_text(json.dumps(k25, ensure_ascii=False, indent=2), "utf-8")
    print(f"  Saved catalog to {CATALOG_PATH}")
    return k25


# ── Step 2: Crawl votes from Knesset REST API ──

def fetch_vote(vote_id):
    """Fetch a single vote by ID."""
    url = f"{VOTES_API}/{vote_id}"
    req = urllib.request.Request(url)
    for k, v in HEADERS.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def parse_vote(vid, data):
    """Parse a vote API response into a bill dict."""
    if not data or not data.get("VoteHeader"):
        return None

    header = data["VoteHeader"][0]
    if header.get("FK_Knesset") != TARGET_KNESSET:
        return None

    item_title = (header.get("ItemTitle") or "").strip()
    if not is_bill_vote(item_title):
        return None

    date_str = header.get("VoteDateStr", "")
    try:
        parts = date_str.split(".")
        vote_date = datetime(int(parts[2]), int(parts[1]), int(parts[0])).strftime("%Y-%m-%d")
    except (ValueError, IndexError):
        vote_date = date_str

    item_id = str(header.get("FK_ItemID", vid))
    counters = {c["Title"]: c["countOfResult"] for c in data.get("VoteCounters", [])}

    mk_votes = []
    for mk in data.get("VoteDetails", []):
        title = mk.get("Title", "")
        if title == "בעד":
            result = 1
        elif title == "נגד":
            result = 2
        elif title == "נמנע":
            result = 3
        else:
            continue
        mk_votes.append({
            "mk_name": (mk.get("MkName") or "").strip(),
            "result": result,
            "faction": (mk.get("FactionName") or "").strip(),
        })

    return {
        "id": item_id,
        "name": item_title,
        "vote_date": vote_date,
        "vote_id": vid,
        "is_accepted": 1 if header.get("IsForAccepted") else 0,
        "total_for": counters.get("בעד", 0),
        "total_against": counters.get("נגד", 0),
        "total_abstain": counters.get("נמנע", 0),
        "vote_description": (header.get("Decision") or "").strip(),
        "mk_votes": mk_votes,
    }


def crawl_votes(existing_vote_ids=None):
    """Crawl vote IDs to find K25 bill votes."""
    print(f"\n=== Step 2: Crawling votes ({START_VOTE_ID} → {MIN_VOTE_ID}) ===")

    if existing_vote_ids is None:
        existing_vote_ids = set()

    bills = {}
    skipped = 0
    batch_size = 5
    delay = 0.5

    total_range = START_VOTE_ID - MIN_VOTE_ID
    for batch_start in range(START_VOTE_ID, MIN_VOTE_ID - 1, -batch_size):
        batch_ids = list(range(batch_start, max(batch_start - batch_size, MIN_VOTE_ID - 1), -1))
        # Skip already-known vote IDs
        batch_ids = [vid for vid in batch_ids if vid not in existing_vote_ids]
        if not batch_ids:
            continue

        results = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(fetch_vote, vid): vid for vid in batch_ids}
            for future in as_completed(futures):
                vid = futures[future]
                try:
                    results[vid] = future.result()
                except Exception:
                    results[vid] = None

        for vid, data in results.items():
            bill = parse_vote(vid, data)
            if bill:
                if bill["id"] not in bills:
                    bills[bill["id"]] = bill
                    print(f"  [{vid}] {bill['name'][:70]}", flush=True)
            elif data and data.get("VoteHeader"):
                header = data["VoteHeader"][0]
                if header.get("FK_Knesset") == TARGET_KNESSET:
                    skipped += 1

        time.sleep(delay)

        scanned = START_VOTE_ID - batch_start + batch_size
        if scanned % 200 == 0:
            pct = scanned / total_range * 100
            print(f"  ... {scanned}/{total_range} ({pct:.0f}%), {len(bills)} bills, {skipped} non-bills", flush=True)

    print(f"  Done: {len(bills)} bills found, {skipped} non-bills skipped")
    return list(bills.values())


# ── Step 3: Match votes to catalog bills ──

def normalize_bill_name(name):
    """Normalize a bill name for fuzzy matching."""
    # Remove year/amendment patterns
    name = re.sub(r',?\s*הת?שפ"?[א-ת].*$', '', name)
    name = re.sub(r'\(תיקון[^)]*\)', '', name)
    name = re.sub(r'\(הוראת שעה[^)]*\)', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def match_vote_to_catalog(vote_bill, catalog):
    """Try to match a vote bill to a catalog entry by name similarity."""
    vote_name = normalize_bill_name(vote_bill["name"])
    if not vote_name:
        return None

    best_match = None
    best_score = 0

    for cat_bill in catalog:
        cat_name = normalize_bill_name(cat_bill["name"])
        if not cat_name:
            continue

        # Quick check: share at least one significant word
        vote_words = set(vote_name.split())
        cat_words = set(cat_name.split())
        if not (vote_words & cat_words - {'הצעת', 'חוק', 'של', 'את', 'על', 'לא'}):
            continue

        score = SequenceMatcher(None, vote_name, cat_name).ratio()
        if score > best_score and score > 0.5:
            best_score = score
            best_match = cat_bill

    return best_match


def enrich_from_catalog(vote_bills, catalog):
    """Match vote bills to catalog and add bill_type, bill_id, summary."""
    print(f"\n=== Step 3: Matching {len(vote_bills)} votes to {len(catalog)} catalog bills ===")

    matched = 0
    gov_count = 0
    for bill in vote_bills:
        # Check if already has bill_id from previous runs
        if bill.get("bill_id") and bill.get("bill_type"):
            matched += 1
            continue

        cat = match_vote_to_catalog(bill, catalog)
        if cat:
            bill["bill_id"] = cat["bill_id"]
            bill["bill_type"] = cat["bill_type"]
            if cat["summary"] and not bill.get("summary"):
                bill["summary"] = cat["summary"]
            matched += 1
            if cat["bill_type"] == "ממשלתית":
                gov_count += 1

    print(f"  Matched: {matched}/{len(vote_bills)}")
    print(f"  Government bills found: {gov_count}")
    return vote_bills


# ── Step 4: Fetch documents via OData ──

def odata_get(entity, params):
    """Fetch from OData API."""
    params["$format"] = "json"
    url = f"{PARLIAMENT_BASE}/{entity}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"
    req = urllib.request.Request(url)
    for k, v in HEADERS.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("value", [])
    except Exception:
        return []


def fetch_documents(bills):
    """Fetch document URLs for bills that have bill_id."""
    print(f"\n=== Step 4: Fetching documents ===")
    fetched = 0
    for i, bill in enumerate(bills):
        if bill.get("doc_url"):
            continue

        bid = bill.get("bill_id")
        if not bid:
            # Try name search
            name = bill["name"]
            clean = re.sub(r'["\'\`]', '', name)
            match = re.search(r'(חוק\s+\S+(?:\s+\S+){0,3})', clean)
            if not match:
                bill["doc_url"] = None
                continue
            search_term = match.group(1)[:35]
            try:
                results = odata_get("KNS_Bill", {
                    "$filter": f"KnessetNum eq {TARGET_KNESSET} and substringof('{search_term}',Name)",
                    "$top": "1",
                    "$select": "BillID,Name,SummaryLaw",
                    "$orderby": "BillID desc",
                })
                if results:
                    bid = results[0]["BillID"]
                    bill["bill_id"] = bid
                    if not bill.get("summary"):
                        bill["summary"] = results[0].get("SummaryLaw") or ""
            except Exception:
                pass

        if bid:
            try:
                docs = odata_get("KNS_DocumentBill", {
                    "$filter": f"BillID eq {bid}",
                    "$top": "1",
                    "$select": "FilePath",
                    "$orderby": "DocumentBillID desc",
                })
                bill["doc_url"] = docs[0]["FilePath"] if docs else None
                fetched += 1
            except Exception:
                bill["doc_url"] = None
        else:
            bill["doc_url"] = None

        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(bills)}...", flush=True)
        time.sleep(0.3)

    print(f"  Fetched {fetched} new document URLs")
    return bills


# ── Main ──

def main():
    votes_only = "--votes-only" in sys.argv
    catalog_only = "--catalog-only" in sys.argv

    print(f"Knesset {TARGET_KNESSET} full bill fetch")
    print(f"Timestamp: {datetime.now().isoformat()}")

    # Load existing bills
    existing = []
    if BILLS_PATH.exists():
        existing = json.loads(BILLS_PATH.read_text("utf-8"))
    existing_by_id = {str(b["id"]): b for b in existing}
    existing_vote_ids = {b["vote_id"] for b in existing}
    print(f"Existing: {len(existing)} bills ({len(existing_vote_ids)} vote IDs)")

    # Step 1: Bill catalog
    catalog = []
    if not votes_only:
        catalog = fetch_bill_catalog()
        if catalog_only:
            return

    # Step 2: Crawl votes
    new_bills = crawl_votes(existing_vote_ids)
    print(f"\nNew bills from crawl: {len(new_bills)}")

    # Merge new into existing
    added = 0
    for bill in new_bills:
        bid = str(bill["id"])
        if bid not in existing_by_id:
            existing_by_id[bid] = bill
            added += 1
        else:
            # Update mk_votes if new bill has more voters
            old = existing_by_id[bid]
            if len(bill.get("mk_votes", [])) > len(old.get("mk_votes", [])):
                old["mk_votes"] = bill["mk_votes"]
    print(f"Added {added} new bills (total: {len(existing_by_id)})")

    all_bills = list(existing_by_id.values())

    # Step 3: Match to catalog for bill types
    if catalog:
        all_bills = enrich_from_catalog(all_bills, catalog)

    # Step 4: Fetch documents
    all_bills = fetch_documents(all_bills)

    # Sort by date
    all_bills.sort(key=lambda b: b.get("vote_date", ""), reverse=True)

    # Stats
    by_type = {}
    for b in all_bills:
        t = b.get("bill_type", "")
        by_type[t] = by_type.get(t, 0) + 1

    print(f"\n=== Final stats ===")
    print(f"Total bills: {len(all_bills)}")
    print(f"By type: {by_type}")
    print(f"With docs: {sum(1 for b in all_bills if b.get('doc_url'))}")
    print(f"With synopsis: {sum(1 for b in all_bills if b.get('synopsis'))}")

    # Save
    BILLS_PATH.write_text(json.dumps(all_bills, ensure_ascii=False, indent=2), "utf-8")
    print(f"\nSaved {len(all_bills)} bills to {BILLS_PATH}")


if __name__ == "__main__":
    main()
