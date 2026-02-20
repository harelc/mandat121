#!/usr/bin/env python3
"""
Fetch pre-first-reading (דיון מוקדם) bills from the Knesset OData v4 API.

These are bills that were voted on in plenum before reaching committee —
most are rejected opposition bills. Adds them to existing bills.json.

Usage:
    python3 fetch_prefirst_bills.py
"""

import json
import time
import sys
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
BILLS_PATH = DATA_DIR / "bills.json"

ODATA_BASE = "https://knesset.gov.il/OdataV4/ParliamentInfo"

# Knesset 23 started 2020-03-16
VOTE_DATE_START = "2020-03-01T00:00:00Z"

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

DELAY = 0.55
MAX_NEW_BILLS = 600  # Only fetch this many newest bills


# ── OData helpers (same as fetch_all_bills.py) ──

def odata_get(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url)
            for k, v in HEADERS.items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if attempt < retries - 1:
                wait = (attempt + 1) * 5
                print(f"    Retry {attempt + 1}/{retries} after {wait}s: {e}", flush=True)
                time.sleep(wait)
            else:
                raise


def odata_query(entity, params=None):
    if params:
        qs = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        url = f"{ODATA_BASE}/{entity}?{qs}"
    else:
        url = f"{ODATA_BASE}/{entity}"
    data = odata_get(url)
    return data.get("value", []), data.get("@odata.nextLink")


def odata_fetch_all(entity, params=None, progress=False, page_size=100):
    """Fetch all pages using explicit $skip/$top (nextLink can hang)."""
    all_items = []
    base_params = dict(params or {})
    base_params["$top"] = str(page_size)
    skip = 0
    page = 0
    while True:
        base_params["$skip"] = str(skip)
        items, _ = odata_query(entity, base_params)
        all_items.extend(items)
        page += 1
        if progress:
            print(f"    page {page}, {len(all_items)} items...", flush=True)
        if len(items) < page_size:
            break
        skip += page_size
        time.sleep(DELAY)
    if progress:
        print(f"    done: {page} pages, {len(all_items)} items", flush=True)
    return all_items


def knesset_num_from_date(date_str):
    if not date_str:
        return 25
    d = date_str[:10]
    for knum, start, end in KNESSET_DATES:
        if start <= d < end:
            return knum
    return 25


def normalize_mk_name(name):
    name = name.replace("'", "").replace("\u05F3", "")
    name = " ".join(name.split())
    return name


MK_NAME_ALIASES = {
    "גנץ בנימין": "גנץ בני",
    "מלקו צגה": "מלקו צגה צגנש",
    "סטרוק אורית מלכה": "סטרוק אורית",
    "פינדרוס יצחק": "פינדרוס יצחק זאב",
}


def load_faction_lookup():
    mk_photos_path = DATA_DIR / "mk_photos.json"
    if not mk_photos_path.exists():
        return {}, {}
    mk_photos = json.loads(mk_photos_path.read_text("utf-8"))
    factions = {}
    name_map = {}
    for name, data in mk_photos.items():
        norm = normalize_mk_name(name)
        factions[norm] = data.get("faction", "")
        name_map[norm] = name
    return factions, name_map


# ── Main ──

def main():
    print(f"Knesset pre-first-reading bill fetch (K23-25)")
    print(f"Timestamp: {datetime.now().isoformat()}")

    try:
        # Load existing bills to know what we already have
        existing_bills = json.loads(BILLS_PATH.read_text("utf-8"))
        existing_ids = {str(b["id"]) for b in existing_bills}
        print(f"Existing bills: {len(existing_bills)} ({len(existing_ids)} unique IDs)")

        # Faction lookup
        factions, name_map = load_faction_lookup()
        print(f"MK factions loaded: {len(factions)}")

        # Step 1: Fetch pre-first-reading votes (server-side filter)
        # Must set $top to trigger pagination (API returns no nextLink without it)
        print("\n=== Step 1: Fetching pre-first-reading votes ===")
        prefirst = odata_fetch_all("KNS_PlenumVote", {
            "$filter": (
                f"VoteDateTime gt {VOTE_DATE_START}"
                " and contains(ForOptionDesc,'\u05dc\u05e7\u05e8\u05d9\u05d0\u05d4 \u05e8\u05d0\u05e9\u05d5\u05e0\u05d4')"
            ),
            "$select": "Id,VoteDateTime,ItemID,VoteTitle,ForOptionDesc",
            "$top": "2000",
        }, progress=True)
        print(f"  Pre-first-reading votes: {len(prefirst)}")

        # Tag reading type
        for v in prefirst:
            v["_reading"] = 0  # 0 = pre-first-reading (דיון מוקדם)

        # Skip bills we already have
        new_votes = [v for v in prefirst if v.get("ItemID") and str(v["ItemID"]) not in existing_ids]
        print(f"  New (not already in bills.json): {len(new_votes)}")

        # For bills with multiple pre-first votes, keep the latest
        best_vote = {}
        for v in new_votes:
            item_id = v["ItemID"]
            existing = best_vote.get(item_id)
            if not existing or (v.get("VoteDateTime") or "") > (existing.get("VoteDateTime") or ""):
                best_vote[item_id] = v
        new_votes = list(best_vote.values())
        # Sort by date descending (newest first), limit to MAX_NEW_BILLS
        new_votes.sort(key=lambda v: v.get("VoteDateTime", ""), reverse=True)
        if len(new_votes) > MAX_NEW_BILLS:
            new_votes = new_votes[:MAX_NEW_BILLS]
        print(f"  Unique new bills: {len(new_votes)}")

        if not new_votes:
            print("Nothing new to fetch!")
            return

        # Step 2: Fetch per-MK vote results (one at a time for reliability)
        print(f"\n=== Step 2: Fetching vote results for {len(new_votes)} votes ===")
        vote_results = {}

        for i, vote in enumerate(new_votes):
            vid = vote["Id"]
            try:
                items, _ = odata_query("KNS_PlenumVoteResult", {
                    "$filter": f"VoteID eq {vid}",
                    "$select": "VoteID,MkId,ResultCode,ResultDesc,LastName,FirstName",
                    "$top": "1000",
                })
                vote_results[vid] = items
            except Exception as e:
                print(f"  Warning: failed vote {vid}: {e}", flush=True)

            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(new_votes)} votes...", flush=True)
            time.sleep(DELAY)

        print(f"  Got results for {len(vote_results)} votes")

        # Step 3: Fetch bill metadata
        item_ids = list({v["ItemID"] for v in new_votes})
        print(f"\n=== Step 3: Fetching bill metadata for {len(item_ids)} bills ===")
        bill_meta = {}

        for i in range(0, len(item_ids), 10):
            batch = item_ids[i:i + 10]
            id_list = ",".join(str(bid) for bid in batch)
            try:
                items, _ = odata_query("KNS_Bill", {
                    "$filter": f"Id in ({id_list})",
                    "$select": "Id,Name,KnessetNum,SubTypeID,SubTypeDesc,SummaryLaw",
                })
                for item in items:
                    bill_meta[item["Id"]] = item
            except Exception as e:
                print(f"  Warning: bill metadata batch failed: {e}", flush=True)

            done = min(i + 10, len(item_ids))
            if done % 50 == 0 or done == len(item_ids):
                print(f"  {done}/{len(item_ids)} bills...", flush=True)
            time.sleep(DELAY)

        print(f"  Got metadata for {len(bill_meta)} bills")

        # Step 4: Fetch bill documents
        print(f"\n=== Step 4: Fetching documents for {len(item_ids)} bills ===")
        doc_urls = {}

        for i in range(0, len(item_ids), 10):
            batch = item_ids[i:i + 10]
            id_list = ",".join(str(bid) for bid in batch)
            try:
                items, _ = odata_query("KNS_DocumentBill", {
                    "$filter": f"BillID in ({id_list})",
                    "$select": "BillID,GroupTypeID,FilePath",
                })
                by_bill = {}
                for it in items:
                    bid = it["BillID"]
                    if bid not in by_bill:
                        by_bill[bid] = {}
                    by_bill[bid][it["GroupTypeID"]] = it["FilePath"]
                for bid, by_type in by_bill.items():
                    doc_urls[bid] = by_type.get(2) or by_type.get(4) or list(by_type.values())[-1]
            except Exception as e:
                print(f"  Warning: doc batch failed: {e}", flush=True)

            done = min(i + 10, len(item_ids))
            if done % 50 == 0 or done == len(item_ids):
                print(f"  {done}/{len(item_ids)} docs ({len(doc_urls)} found)...", flush=True)
            time.sleep(DELAY)

        print(f"  Got {len(doc_urls)} document URLs")

        # Step 5: Assemble new bill entries
        print(f"\n=== Step 5: Assembling {len(new_votes)} new bills ===")
        new_bills = []

        for vote in new_votes:
            item_id = vote["ItemID"]
            vote_id = vote["Id"]
            bill = bill_meta.get(item_id, {})
            knesset_num = bill.get("KnessetNum") or knesset_num_from_date(
                vote.get("VoteDateTime", "")
            )

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

                mk_name = MK_NAME_ALIASES.get(mk_name, mk_name)
                norm_name = normalize_mk_name(mk_name)

                if norm_name in name_map:
                    mk_name = name_map[norm_name]

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

            name = bill.get("Name") or vote.get("VoteTitle", "")
            sub_type_id = bill.get("SubTypeID")
            bill_type = BILL_TYPES.get(sub_type_id, bill.get("SubTypeDesc", ""))
            doc_url = doc_urls.get(item_id)

            new_bills.append({
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
                "vote_reading": 0,  # pre-first-reading
            })

        # Merge with existing
        all_bills = existing_bills + new_bills
        all_bills.sort(key=lambda b: b.get("vote_date", ""), reverse=True)

        # Stats
        by_knesset = {}
        by_reading = {}
        accepted = 0
        rejected = 0
        for b in all_bills:
            kn = b.get("knesset_num", "?")
            by_knesset[kn] = by_knesset.get(kn, 0) + 1
            r = b.get("vote_reading", "?")
            by_reading[r] = by_reading.get(r, 0) + 1
            if b.get("is_accepted"):
                accepted += 1
            else:
                rejected += 1

        print(f"\n=== Final stats ===")
        print(f"Total bills: {len(all_bills)} (was {len(existing_bills)}, added {len(new_bills)})")
        print(f"By Knesset: {dict(sorted(by_knesset.items()))}")
        print(f"By reading: {by_reading}")
        print(f"Accepted: {accepted}, Rejected: {rejected}")
        print(f"With synopsis: {sum(1 for b in all_bills if b.get('synopsis'))}")
        print(f"With docs: {sum(1 for b in all_bills if b.get('doc_url'))}")

        # Save
        BILLS_PATH.write_text(
            json.dumps(all_bills, ensure_ascii=False, indent=2), "utf-8"
        )
        print(f"\nSaved {len(all_bills)} bills to {BILLS_PATH}")

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        import traceback
        print(f"\nERROR: {e}", flush=True)
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(1)


if __name__ == "__main__":
    main()
