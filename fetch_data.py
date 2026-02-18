#!/usr/bin/env python3
"""
Fetch Knesset 25 bill votes from the undocumented Knesset REST API.
Crawls vote IDs to find recent votes, extracts per-MK breakdowns.
Filters to actual bills only (not no-confidence, procedural motions, etc.)
and keeps only bills that have a document (bill text) available.

Data sources:
  Votes: GET https://knesset.gov.il/WebSiteApi/knessetapi/Votes/GetVoteDetails/{voteId}
  Bills/Docs: https://knesset.gov.il/Odata/ParliamentInfo.svc/
"""

import json
import re
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

VOTES_API = "https://knesset.gov.il/WebSiteApi/knessetapi/Votes/GetVoteDetails"
PARLIAMENT_BASE = "https://knesset.gov.il/Odata/ParliamentInfo.svc"

TARGET_KNESSET = 25
MAX_BILLS = 250
START_VOTE_ID = 45250
MIN_VOTE_ID = 34526

# Patterns that indicate this is NOT a regular bill
NON_BILL_PATTERNS = [
    r'הצעת אי[\-\s]?אמון',      # no-confidence motion
    r'אי[\-\s]?אמון',            # no-confidence
    r'הודעת ראש הממשלה',        # PM announcement
    r'הודעת הממשלה',            # government announcement
    r'בחירת\s',                  # election of (speaker, etc.)
    r'מינוי\s',                  # appointment
    r'הרכב\s+ועד',              # committee composition
    r'הצעה לסדר היום',          # motion for the agenda
    r'הצעת סיכום',              # summary motion
    r'דיון\s+מיוחד',            # special debate
    r'שאילתות',                 # parliamentary questions
]
NON_BILL_RE = re.compile('|'.join(NON_BILL_PATTERNS))


def is_bill(name):
    """Check if this vote item is an actual bill (הצעת חוק)."""
    if not name:
        return False
    # Must contain "חוק" (law) somewhere
    if 'חוק' not in name:
        return False
    # Must not match non-bill patterns
    if NON_BILL_RE.search(name):
        return False
    return True


def fetch_vote(vote_id):
    """Fetch a single vote by ID from the Knesset API."""
    url = f"{VOTES_API}/{vote_id}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except Exception:
        return None


def odata_get(entity, params=None):
    """Fetch from OData ParliamentInfo API."""
    url = f"{PARLIAMENT_BASE}/{entity}"
    if params is None:
        params = {}
    params["$format"] = "json"
    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    full_url = f"{url}?{query}"
    req = urllib.request.Request(full_url)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("value", [])
    except Exception:
        return []


def crawl_votes():
    """Crawl vote IDs from newest to oldest, collecting K25 bill votes."""
    print(f"\n=== Crawling Knesset {TARGET_KNESSET} votes ===")

    bills = {}  # sess_item_id -> bill data
    seen_vote_ids = set()
    skipped_non_bills = 0

    batch_size = 20
    current_id = START_VOTE_ID
    empty_streak = 0
    total_fetched = 0

    # We need more than MAX_BILLS raw items because we'll filter later
    raw_target = MAX_BILLS * 3

    while current_id >= MIN_VOTE_ID and len(bills) < raw_target:
        batch_ids = list(range(current_id, max(current_id - batch_size, MIN_VOTE_ID - 1), -1))
        batch_ids = [vid for vid in batch_ids if vid not in seen_vote_ids]

        if not batch_ids:
            current_id -= batch_size
            continue

        results = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(fetch_vote, vid): vid for vid in batch_ids}
            for future in as_completed(futures):
                vid = futures[future]
                seen_vote_ids.add(vid)
                try:
                    results[vid] = future.result()
                except Exception:
                    results[vid] = None

        found_in_batch = 0
        for vid in sorted(results.keys(), reverse=True):
            data = results[vid]
            if not data or not data.get("VoteHeader"):
                continue

            header = data["VoteHeader"][0]
            knesset = header.get("FK_Knesset")
            if knesset != TARGET_KNESSET:
                continue

            item_title = (header.get("ItemTitle") or "").strip()

            # Filter: only actual bills
            if not is_bill(item_title):
                skipped_non_bills += 1
                continue

            # Parse date
            date_str = header.get("VoteDateStr", "")
            try:
                parts = date_str.split(".")
                vote_dt = datetime(int(parts[2]), int(parts[1]), int(parts[0]))
                vote_date = vote_dt.strftime("%Y-%m-%d")
            except (ValueError, IndexError):
                vote_date = date_str

            item_id = str(header.get("FK_ItemID", vid))
            is_accepted = header.get("IsForAccepted", False)

            # Parse counters
            counters = {c["Title"]: c["countOfResult"] for c in data.get("VoteCounters", [])}
            total_for = counters.get("בעד", 0)
            total_against = counters.get("נגד", 0)
            total_abstain = counters.get("נמנע", 0)

            # Parse MK details
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

            if item_id not in bills:
                bills[item_id] = {
                    "id": item_id,
                    "name": item_title,
                    "vote_date": vote_date,
                    "vote_id": vid,
                    "is_accepted": 1 if is_accepted else 0,
                    "total_for": total_for,
                    "total_against": total_against,
                    "total_abstain": total_abstain,
                    "vote_description": (header.get("Decision") or "").strip(),
                    "mk_votes": mk_votes,
                }
                found_in_batch += 1

            total_fetched += 1

        if found_in_batch > 0:
            empty_streak = 0
        else:
            empty_streak += 1

        if empty_streak > 30:
            print(f"  Stopping: {empty_streak} consecutive empty batches")
            break

        current_id -= batch_size
        if total_fetched % 50 == 0 or found_in_batch > 0:
            print(f"  Scanned to vote ID {current_id}, found {len(bills)} bills ({skipped_non_bills} non-bills skipped)")

    print(f"  Total: {len(bills)} bills found, {skipped_non_bills} non-bills skipped")
    sorted_bills = sorted(bills.values(), key=lambda b: b["vote_date"], reverse=True)
    return sorted_bills


def fetch_bill_documents(bills):
    """Match bill names to KNS_Bill for document links and summaries."""
    print("\n=== Fetching bill documents from OData ===")
    for i, bill in enumerate(bills):
        name = bill["name"]
        clean = re.sub(r'["\'\`]', '', name)
        match = re.search(r'(חוק\s+\S+(?:\s+\S+){0,3})', clean)
        if not match:
            bill["doc_url"] = None
            bill["summary"] = ""
            continue

        search_term = match.group(1)[:35]
        try:
            results = odata_get(
                "KNS_Bill",
                {
                    "$filter": f"KnessetNum eq {TARGET_KNESSET} and substringof('{search_term}',Name)",
                    "$top": "1",
                    "$select": "BillID,Name,SummaryLaw",
                    "$orderby": "BillID desc",
                },
            )
            if results:
                bill_id = results[0]["BillID"]
                bill["bill_id"] = bill_id
                bill["summary"] = results[0].get("SummaryLaw") or ""
                docs = odata_get(
                    "KNS_DocumentBill",
                    {
                        "$filter": f"BillID eq {bill_id}",
                        "$top": "1",
                        "$select": "FilePath",
                        "$orderby": "DocumentBillID desc",
                    },
                )
                bill["doc_url"] = docs[0]["FilePath"] if docs else None
            else:
                bill["doc_url"] = None
                bill["summary"] = ""
        except Exception as e:
            print(f"  Error matching '{name[:40]}': {e}")
            bill["doc_url"] = None
            bill["summary"] = ""

        if (i + 1) % 20 == 0:
            print(f"  Processed {i + 1}/{len(bills)} bills")

    matched = sum(1 for b in bills if b.get("doc_url"))
    print(f"  Found documents for {matched}/{len(bills)} bills")
    return bills


def main():
    print(f"Starting Knesset {TARGET_KNESSET} data fetch...")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Target: {MAX_BILLS} bills with documents")

    # Step 1: Crawl votes (get more than needed, we'll filter)
    all_bills = crawl_votes()
    if not all_bills:
        print("No bills found!")
        return

    # Step 2: Fetch bill documents
    all_bills = fetch_bill_documents(all_bills)

    # Step 3: Keep only bills WITH a doc_url, up to MAX_BILLS
    bills_with_docs = [b for b in all_bills if b.get("doc_url")]
    bills_without = [b for b in all_bills if not b.get("doc_url")]
    print(f"\n  Bills with documents: {len(bills_with_docs)}")
    print(f"  Bills without documents: {len(bills_without)}")

    # Prioritize bills with docs, then fill up with others if needed
    final = bills_with_docs[:MAX_BILLS]
    if len(final) < MAX_BILLS:
        final += bills_without[:MAX_BILLS - len(final)]

    # Step 4: Save
    out_path = DATA_DIR / "bills.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)
    print(f"\nDone! Saved {len(final)} bills to {out_path} ({sum(1 for b in final if b.get('doc_url'))} with documents)")


if __name__ == "__main__":
    main()
