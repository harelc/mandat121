#!/usr/bin/env python3
"""
Gentle crawl for more K25 bills. Scans vote IDs with low concurrency
and delays to avoid Radware bot detection.

Usage: python3 crawl_more.py [start_id] [end_id]
Default: scans 45800 → 39000

Merges new bills into existing bills.json (doesn't overwrite).
"""

import json
import re
import time
import urllib.request
import urllib.parse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

DATA_DIR = Path(__file__).parent / "data"
VOTES_API = "https://knesset.gov.il/WebSiteApi/knessetapi/Votes/GetVoteDetails"
PARLIAMENT_BASE = "https://knesset.gov.il/Odata/ParliamentInfo.svc"
TARGET_KNESSET = 25

NON_BILL_PATTERNS = [
    r'הצעת אי[\-\s]?אמון', r'אי[\-\s]?אמון', r'הודעת ראש הממשלה',
    r'הודעת הממשלה', r'בחירת\s', r'מינוי\s', r'הרכב\s+ועד',
    r'הצעה לסדר היום', r'הצעת סיכום', r'דיון\s+מיוחד', r'שאילתות',
]
NON_BILL_RE = re.compile('|'.join(NON_BILL_PATTERNS))


def is_bill(name):
    if not name:
        return False
    if 'חוק' not in name and 'ועדה' not in name and 'המלצ' not in name:
        return False
    if NON_BILL_RE.search(name):
        return False
    return True


def fetch_vote(vote_id):
    url = f"{VOTES_API}/{vote_id}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0")
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
    if not is_bill(item_title):
        return None

    from datetime import datetime
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


def odata_get(entity, params):
    params["$format"] = "json"
    url = f"{PARLIAMENT_BASE}/{entity}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("value", [])
    except Exception:
        return []


def fetch_doc_for_bill(bill):
    """Try to find doc_url via OData."""
    name = bill["name"]
    clean = re.sub(r'["\'\`]', '', name)
    match = re.search(r'(חוק\s+\S+(?:\s+\S+){0,3})', clean)
    if not match:
        return
    search_term = match.group(1)[:35]
    try:
        results = odata_get("KNS_Bill", {
            "$filter": f"KnessetNum eq 25 and substringof('{search_term}',Name)",
            "$top": "1",
            "$select": "BillID,Name,SummaryLaw",
            "$orderby": "BillID desc",
        })
        if results:
            bill_id = results[0]["BillID"]
            bill["bill_id"] = bill_id
            bill["summary"] = results[0].get("SummaryLaw") or ""
            docs = odata_get("KNS_DocumentBill", {
                "$filter": f"BillID eq {bill_id}",
                "$top": "1",
                "$select": "FilePath",
                "$orderby": "DocumentBillID desc",
            })
            bill["doc_url"] = docs[0]["FilePath"] if docs else None
        else:
            bill["doc_url"] = None
            bill["summary"] = ""
    except Exception:
        bill["doc_url"] = None
        bill["summary"] = ""


def main():
    import sys
    start_id = int(sys.argv[1]) if len(sys.argv) > 1 else 45800
    end_id = int(sys.argv[2]) if len(sys.argv) > 2 else 39000

    # Load existing bills to avoid duplicates
    bills_path = DATA_DIR / "bills.json"
    existing = json.loads(bills_path.read_text("utf-8"))
    existing_ids = {str(b["id"]) for b in existing}
    print(f"Existing: {len(existing)} bills ({len(existing_ids)} unique IDs)")
    print(f"Scanning vote IDs {start_id} → {end_id}...", flush=True)

    new_bills = {}
    skipped = 0

    # Scan in small batches with delays
    batch_size = 5
    for batch_start in range(start_id, end_id - 1, -batch_size):
        batch_ids = list(range(batch_start, max(batch_start - batch_size, end_id - 1), -1))

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
                if bill["id"] not in existing_ids and bill["id"] not in new_bills:
                    new_bills[bill["id"]] = bill
                    print(f"  NEW: [{vid}] {bill['name'][:60]}", flush=True)
            elif data and data.get("VoteHeader"):
                header = data["VoteHeader"][0]
                if header.get("FK_Knesset") == TARGET_KNESSET:
                    skipped += 1

        # Be gentle — 0.5s delay between batches
        time.sleep(0.5)

        scanned = start_id - batch_start + batch_size
        if scanned % 100 == 0:
            print(f"  ... scanned {scanned} IDs, {len(new_bills)} new bills, {skipped} non-bills", flush=True)

    print(f"\nFound {len(new_bills)} new bills", flush=True)

    if new_bills:
        # Fetch documents for new bills
        print("Fetching documents...", flush=True)
        for i, bill in enumerate(new_bills.values()):
            fetch_doc_for_bill(bill)
            time.sleep(0.3)
            if (i + 1) % 10 == 0:
                print(f"  ... {i+1}/{len(new_bills)}", flush=True)

        # Merge into existing
        existing.extend(new_bills.values())
        existing.sort(key=lambda b: b.get("vote_date", ""), reverse=True)
        bills_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), "utf-8")
        print(f"Merged {len(new_bills)} new bills → total {len(existing)}")
    else:
        print("No new bills to add")


if __name__ == "__main__":
    main()
