#!/usr/bin/env python3
"""
Fetch Knesset 23/24/25 bills using the official OData v4 API only.

Fetches קריאה שניה ושלישית votes, per-MK vote breakdowns, bill metadata,
and documents. Produces data/bills.json.

Usage:
    python3 fetch_all_bills.py          # incremental: only votes since last run
    python3 fetch_all_bills.py --full   # full rebuild from 2020-03-01
"""

import json
import time
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
BILLS_PATH = DATA_DIR / "bills.json"

ODATA_BASE = "https://knesset.gov.il/OdataV4/ParliamentInfo"

# Knesset 23 started 2020-03-16 — full-rebuild start date
VOTE_DATE_START = "2020-03-01T00:00:00Z"

# Incremental fetch: re-check this many days before the latest stored vote,
# so late vote corrections / status changes get picked up on the next run.
OVERLAP_DAYS = 7

# Bill SubTypeID → Hebrew label
BILL_TYPES = {53: "ממשלתית", 54: "פרטית", 55: "ועדה"}

# ResultCode → numeric result for output
RESULT_MAP = {7: 1, 8: 2, 9: 3}  # 7=בעד→1, 8=נגד→2, 9=נמנע→3

# Knesset date ranges for fallback knesset_num inference
KNESSET_DATES = [
    (23, "2020-03-16", "2021-06-13"),
    (24, "2021-06-13", "2022-06-30"),
    (25, "2022-11-15", "2030-01-01"),
]

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
}

DELAY = 0.1  # seconds between API calls


# ── OData helpers ──

def odata_get(url):
    """Fetch a single OData URL and return the JSON response."""
    req = urllib.request.Request(url)
    for k, v in HEADERS.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def odata_query(entity, params=None):
    """Build and fetch an OData query, returning the value list."""
    if params:
        qs = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        url = f"{ODATA_BASE}/{entity}?{qs}"
    else:
        url = f"{ODATA_BASE}/{entity}"
    data = odata_get(url)
    return data.get("value", []), data.get("@odata.nextLink")


def odata_fetch_all(entity, params=None):
    """Fetch all pages of an OData query."""
    all_items = []
    items, next_link = odata_query(entity, params)
    all_items.extend(items)
    while next_link:
        time.sleep(DELAY)
        data = odata_get(next_link)
        all_items.extend(data.get("value", []))
        next_link = data.get("@odata.nextLink")
    return all_items


def knesset_num_from_date(date_str):
    """Infer Knesset number from a date string."""
    if not date_str:
        return 25
    d = date_str[:10]
    for knum, start, end in KNESSET_DATES:
        if start <= d < end:
            return knum
    return 25


# ── Step 1: MK faction lookup from mk_photos.json ──

MK_PHOTOS_PATH = DATA_DIR / "mk_photos.json"


def normalize_mk_name(name):
    """Normalize MK name for matching: strip geresh, extra spaces, etc."""
    name = name.replace("'", "").replace("\u05F3", "")  # remove geresh
    name = " ".join(name.split())  # collapse multiple spaces
    return name


# OData vote names → mk_photos.json canonical names
# (for current K25 MKs whose names differ between OData and mk_photos)
MK_NAME_ALIASES = {
    "גנץ בנימין": "גנץ בני",
    "מלקו צגה": "מלקו צגה צגנש",
    "סטרוק אורית מלכה": "סטרוק אורית",
    "פינדרוס יצחק": "פינדרוס יצחק זאב",
}


def load_faction_lookup():
    """Build name→faction from mk_photos.json (current K25 MKs)."""
    print("\n=== Step 1: Loading MK faction lookup from mk_photos.json ===")

    if not MK_PHOTOS_PATH.exists():
        print("  WARNING: mk_photos.json not found!")
        return {}, {}

    mk_photos = json.loads(MK_PHOTOS_PATH.read_text("utf-8"))

    # Build normalized name → faction map
    factions = {}  # normalized_name → faction
    name_map = {}  # normalized_name → canonical name (from mk_photos)
    for name, data in mk_photos.items():
        norm = normalize_mk_name(name)
        factions[norm] = data.get("faction", "")
        name_map[norm] = name

    print(f"  MKs loaded: {len(factions)}")
    return factions, name_map


# ── Step 2: Fetch 2nd+3rd reading votes ──

def compute_start_date(existing_path, overlap_days=OVERLAP_DAYS):
    """Incremental start date: latest stored 2nd/3rd-reading vote minus an
    overlap window. Returns None (→ full rebuild) if no usable data exists."""
    if not existing_path.exists():
        return None
    try:
        bills = json.loads(existing_path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    # Only this script's domain (2nd/3rd reading); pre-first dates are handled
    # by fetch_prefirst_bills.py and would otherwise skew the window.
    dates = [
        b["vote_date"] for b in bills
        if b.get("vote_reading") in (2, 3) and b.get("vote_date")
    ]
    if not dates:
        return None
    latest = datetime.strptime(max(dates), "%Y-%m-%d")
    start = latest - timedelta(days=overlap_days)
    return start.strftime("%Y-%m-%dT00:00:00Z")


def fetch_votes(start_date=None):
    """Fetch 2nd and 3rd reading votes from K23-25.

    start_date: ISO timestamp to fetch votes after. None → full rebuild
    from VOTE_DATE_START.
    """
    print("\n=== Step 2: Fetching 2nd+3rd reading votes ===")
    since = start_date or VOTE_DATE_START
    mode = "incremental" if start_date else "full"
    print(f"  Mode: {mode}, votes after {since}")
    votes = odata_fetch_all("KNS_PlenumVote", {
        "$filter": (
            f"VoteDateTime gt {since}"
            " and (contains(ForOptionDesc,'קריאה שלישית')"
            " or contains(ForOptionDesc,'לקבל בקריאה שנייה'))"
        ),
        "$orderby": "VoteDateTime desc",
    })
    print(f"  Total votes: {len(votes)}")

    # Classify as 2nd or 3rd reading
    for v in votes:
        desc = v.get("ForOptionDesc", "")
        v["_reading"] = 3 if "קריאה שלישית" in desc else 2

    # Count readings
    r2 = sum(1 for v in votes if v["_reading"] == 2)
    r3 = sum(1 for v in votes if v["_reading"] == 3)
    print(f"  2nd reading: {r2}, 3rd reading: {r3}")
    return votes


# ── Step 3: Fetch per-MK vote results ──

def fetch_vote_results(vote_ids):
    """Fetch per-MK results for a list of vote IDs, batched."""
    print(f"\n=== Step 3: Fetching vote results for {len(vote_ids)} votes ===")
    results = {}  # VoteID → [results]
    batch_size = 10

    for i in range(0, len(vote_ids), batch_size):
        batch = vote_ids[i:i + batch_size]
        id_list = ",".join(str(vid) for vid in batch)
        items = odata_fetch_all("KNS_PlenumVoteResult", {
            "$filter": f"VoteID in ({id_list})",
            "$select": "VoteID,MkId,ResultCode,ResultDesc,LastName,FirstName",
            "$top": "1000",
        })
        for item in items:
            vid = item["VoteID"]
            if vid not in results:
                results[vid] = []
            results[vid].append(item)

        if (i // batch_size + 1) % 5 == 0:
            print(f"  {i + batch_size}/{len(vote_ids)} votes...", flush=True)
        time.sleep(DELAY)

    print(f"  Got results for {len(results)} votes")
    return results


# ── Step 4: Fetch bill metadata ──

def fetch_bills(item_ids):
    """Fetch bill metadata for a list of ItemIDs (= KNS_Bill.Id), batched."""
    print(f"\n=== Step 4: Fetching bill metadata for {len(item_ids)} bills ===")
    bills = {}
    batch_size = 20

    for i in range(0, len(item_ids), batch_size):
        batch = item_ids[i:i + batch_size]
        id_list = ",".join(str(bid) for bid in batch)
        items = odata_fetch_all("KNS_Bill", {
            "$filter": f"Id in ({id_list})",
            "$select": "Id,Name,KnessetNum,SubTypeID,SubTypeDesc,SummaryLaw",
        })
        for item in items:
            bills[item["Id"]] = item

        if (i // batch_size + 1) % 10 == 0:
            print(f"  {i + batch_size}/{len(item_ids)} bills...", flush=True)
        time.sleep(DELAY)

    print(f"  Got metadata for {len(bills)} bills")
    return bills


# ── Step 5: Fetch bill documents ──

def fetch_documents(bill_ids):
    """Fetch latest document URL for each bill ID."""
    print(f"\n=== Step 5: Fetching documents for {len(bill_ids)} bills ===")
    docs = {}

    for i, bid in enumerate(bill_ids):
        try:
            # Fetch all docs, prefer 1st reading (has דברי הסבר) for synopsis
            items, _ = odata_query("KNS_DocumentBill", {
                "$filter": f"BillID eq {bid}",
                "$select": "GroupTypeID,FilePath",
            })
            if items:
                # Prefer: 1st reading (2) > 2nd/3rd reading (4) > latest
                by_type = {it["GroupTypeID"]: it["FilePath"] for it in items}
                docs[bid] = by_type.get(2) or by_type.get(4) or items[-1]["FilePath"]
        except Exception as e:
            if (i + 1) % 50 == 0:
                print(f"  Warning: failed to fetch doc for bill {bid}: {e}")

        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(bill_ids)}...", flush=True)
        time.sleep(DELAY)

    print(f"  Got {len(docs)} document URLs")
    return docs


# ── Step 6: Assemble bills.json ──

def assemble_bills(votes, vote_results, bill_meta, doc_urls, factions, name_map):
    """Assemble the final bills list from all fetched data."""
    print("\n=== Step 6: Assembling bills.json ===")

    # Group votes by ItemID, prefer 3rd reading over 2nd
    best_vote = {}  # ItemID → vote record
    for vote in votes:
        item_id = vote.get("ItemID")
        if not item_id:
            continue
        existing = best_vote.get(item_id)
        if not existing or vote["_reading"] > existing["_reading"]:
            best_vote[item_id] = vote

    print(f"  Unique bills (by ItemID): {len(best_vote)}")

    bills = []
    for item_id, vote in best_vote.items():
        vote_id = vote["Id"]
        bill = bill_meta.get(item_id, {})
        knesset_num = bill.get("KnessetNum") or knesset_num_from_date(
            vote.get("VoteDateTime", "")
        )

        # Build mk_votes from results
        mk_votes = []
        total_for = 0
        total_against = 0
        total_abstain = 0

        for r in vote_results.get(vote_id, []):
            code = r["ResultCode"]
            result = RESULT_MAP.get(code)
            if not result:
                continue

            last = (r.get("LastName") or "").strip()
            first = (r.get("FirstName") or "").strip()
            mk_name = f"{last} {first}".strip()

            # Apply name aliases first (OData → mk_photos canonical)
            mk_name = MK_NAME_ALIASES.get(mk_name, mk_name)
            norm_name = normalize_mk_name(mk_name)

            # Use canonical name from mk_photos if available
            if norm_name in name_map:
                mk_name = name_map[norm_name]

            # Look up faction by normalized name
            faction = factions.get(norm_name, "")

            mk_votes.append({
                "mk_name": mk_name,
                "result": result,
                "faction": faction,
            })

            if result == 1:
                total_for += 1
            elif result == 2:
                total_against += 1
            elif result == 3:
                total_abstain += 1

        # Determine bill name — prefer KNS_Bill.Name, fall back to VoteTitle
        name = bill.get("Name") or vote.get("VoteTitle", "")

        # Bill type
        sub_type_id = bill.get("SubTypeID")
        bill_type = BILL_TYPES.get(sub_type_id, bill.get("SubTypeDesc", ""))

        # Document URL — ItemID is KNS_Bill.Id which is also the BillID for docs
        doc_url = doc_urls.get(item_id)

        bills.append({
            "id": str(item_id),
            "name": name,
            "vote_date": (vote.get("VoteDateTime") or "")[:10],
            "vote_id": vote_id,
            "is_accepted": 1 if total_for > total_against else 0,
            "total_for": total_for,
            "total_against": total_against,
            "total_abstain": total_abstain,
            "mk_votes": mk_votes,
            "bill_id": item_id,
            "bill_type": bill_type,
            "summary": bill.get("SummaryLaw") or "",
            "doc_url": doc_url,
            "knesset_num": knesset_num,
            "vote_reading": vote["_reading"],
        })

    # Sort by date descending
    bills.sort(key=lambda b: b.get("vote_date", ""), reverse=True)
    return bills


def merge_existing(new_bills, existing_path):
    """Merge freshly fetched bills with existing data.

    Each vote (vote_id) is a distinct record, so we key on vote_id:
    - newly fetched votes replace their existing counterpart (refreshed counts)
      while carrying over preserved enrichment (synopsis/topics);
    - existing bills NOT in the new set are kept untouched. This is what makes
      an incremental fetch safe — older bills and pre-first-reading entries
      (which this script never fetches) survive.
    """
    if not existing_path.exists():
        return new_bills

    existing = json.loads(existing_path.read_text("utf-8"))
    existing_by_vote = {b["vote_id"]: b for b in existing if b.get("vote_id")}
    new_vote_ids = {b["vote_id"] for b in new_bills if b.get("vote_id")}

    preserved = 0
    for bill in new_bills:
        old = existing_by_vote.get(bill.get("vote_id"))
        if old:
            if old.get("synopsis") and not bill.get("synopsis"):
                bill["synopsis"] = old["synopsis"]
                preserved += 1
            if old.get("topics") and not bill.get("topics"):
                bill["topics"] = old["topics"]

    # Keep every existing bill the fetch didn't just replace.
    retained = [b for b in existing if b.get("vote_id") not in new_vote_ids]
    merged = retained + new_bills

    print(f"  Preserved {preserved} existing synopses")
    print(f"  Retained {len(retained)} untouched bills, "
          f"{len(new_bills)} fetched ({len(merged)} total)")
    return merged


# ── Main ──

def main():
    full = "--full" in sys.argv

    print(f"Knesset OData v4 bill fetch (K23-25)")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"API: {ODATA_BASE}")

    try:
        # Step 1: Faction lookup
        factions, name_map = load_faction_lookup()

        # Step 2: Fetch votes (incremental by default; --full rebuilds)
        start_date = None if full else compute_start_date(BILLS_PATH)
        votes = fetch_votes(start_date)
        if not votes:
            print("No new votes found.")
            return

        # Step 3: Fetch per-MK results
        vote_ids = [v["Id"] for v in votes]
        vote_results = fetch_vote_results(vote_ids)

        # Step 4: Fetch bill metadata
        item_ids = list({v["ItemID"] for v in votes if v.get("ItemID")})
        bill_meta = fetch_bills(item_ids)

        # Step 5: Fetch documents
        doc_urls = fetch_documents(item_ids)

        # Step 6: Assemble
        bills = assemble_bills(votes, vote_results, bill_meta, doc_urls, factions, name_map)

        # Merge with existing (preserve synopsis/topics)
        bills = merge_existing(bills, BILLS_PATH)

        # Stats
        by_knesset = {}
        by_type = {}
        for b in bills:
            kn = b.get("knesset_num", "?")
            by_knesset[kn] = by_knesset.get(kn, 0) + 1
            t = b.get("bill_type", "")
            by_type[t] = by_type.get(t, 0) + 1

        print(f"\n=== Final stats ===")
        print(f"Total bills: {len(bills)}")
        print(f"By Knesset: {dict(sorted(by_knesset.items()))}")
        print(f"By type: {by_type}")
        print(f"With summary: {sum(1 for b in bills if b.get('summary'))}")
        print(f"With synopsis: {sum(1 for b in bills if b.get('synopsis'))}")
        print(f"With docs: {sum(1 for b in bills if b.get('doc_url'))}")
        print(f"Accepted: {sum(1 for b in bills if b.get('is_accepted'))}")

        # Save
        BILLS_PATH.write_text(
            json.dumps(bills, ensure_ascii=False, indent=2), "utf-8"
        )
        print(f"\nSaved {len(bills)} bills to {BILLS_PATH}")

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        raise


if __name__ == "__main__":
    main()
