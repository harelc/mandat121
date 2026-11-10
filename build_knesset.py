#!/usr/bin/env python3
"""
Full-coverage per-Knesset builder for המנדט ה־121 (index.html).

Unlike fetch_all_bills.py (vote-linked bills only), this builds:
  data/k{N}_bills.json — EVERY bill of the Knesset from KNS_Bill:
      status (+desc), committee, type, initiators (resolved to names),
      publication info, postponement reason, the official bill text
      (KNS_DocumentBill — the נוסח, with its דברי הסבר, exists from the
      day the bill is tabled, long before any vote), merged with any
      known votes/synopsis/topics.
  data/k{N}_bill_docs.json — EVERY official document of every bill
      (KNS_DocumentBill: all נוסח versions, חומר רקע, the published law…),
      keyed by bill id for lazy loading on the bill page. k{N}_bills.json
      keeps only the primary doc_url so the main payload stays lean.
  data/k{N}_votes.json — EVERY plenum vote header of the era, classified
      by kind (readings, reservations, no-confidence, transfers, agenda…)
      with tallies merged from the existing results dataset where known.

Usage: python3 -u build_knesset.py [--knesset 25]
Stdlib only. Explicit $skip/$top pagination throughout.
"""
import argparse
import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

BASE = "https://knesset.gov.il/OdataV4/ParliamentInfo"
HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 mandat121-build"}
DATA = Path(__file__).parent / "data"
DELAY = 0.1
# Era start per Knesset (plenum votes carry no KnessetNum; bills do, and
# votes are additionally kept only when their ItemID is a bill of this
# Knesset OR the vote is a non-bill motion inside the era window).
ERA_START = {25: "2022-11-15T00:00:00Z", 26: "2026-10-01T00:00:00Z"}


def get(entity, params):
    url = f"{BASE}/{entity}?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def fetch_all(entity, params, page=100, label=""):
    out, skip = [], 0
    while True:
        d = get(entity, dict(params, **{"$top": str(page), "$skip": str(skip)}))
        v = d.get("value", [])
        out.extend(v)
        if len(v) < page:
            print(f"  {label or entity}: {len(out)} rows", flush=True)
            return out
        skip += page
        time.sleep(DELAY)


def fetch_in(entity, field, ids, extra, select, batch=50, label=""):
    out, ids = [], list(ids)
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        filt = f"{field} in ({','.join(str(x) for x in chunk)})"
        if extra:
            filt += f" and {extra}"
        out.extend(fetch_all(entity, {"$filter": filt, "$select": select}, label=""))
        if (i // batch) % 10 == 0:
            print(f"  {label}: batch {i//batch+1}/{(len(ids)+batch-1)//batch}, {len(out)} rows", flush=True)
    print(f"  {label}: {len(out)} rows total", flush=True)
    return out


# Official versions of a bill in KNS_DocumentBill, in the order the bill
# page should offer them: the original proposal text first (GroupType 1,
# "לדיון מוקדם" — it carries the דברי הסבר), then the first-reading and
# 2nd–3rd-reading prints, then the published law. PDF beats DOC/DOCX of the
# same version so the bill page can offer its in-page PDF reader.
DOC_GROUP_PREF = (1, 2, 4, 9, 8)


def pick_doc_url(docs):
    if not docs:
        return None
    pdfs = [d for d in docs
            if (d.get("ApplicationDesc") or "").upper() == "PDF"
            or (d.get("FilePath") or "").lower().split("?")[0].endswith(".pdf")]
    pool = pdfs or docs
    rank = {g: i for i, g in enumerate(DOC_GROUP_PREF)}
    pool.sort(key=lambda d: (rank.get(d.get("GroupTypeID"), 99), d.get("Id") or 0))
    return (pool[0].get("FilePath") or "").replace("\\", "/") or None


# Display order for a bill's full document list: the נוסח versions in
# procedural order, then the published law, then supporting material
# (חומר רקע, מסמכי ממ"מ, החלטות ממשלה…) — everything KNS_DocumentBill
# attaches to the bill, as the Knesset site lists them.
DOC_STAGE_ORDER = {1: 0, 51: 1, 2: 2, 3: 3, 4: 4, 5: 5, 46: 6, 139: 7,
                   9: 8, 57: 9, 138: 10, 8: 11, 59: 20, 12: 21, 122: 22, 17: 23}


def build_docs(docs):
    """Deduped, stage-ordered [{group, format, url}] for one bill.

    The same version is often published as DOC + PDF (and occasionally
    as duplicate rows) — one entry per (document group, file stem),
    PDF preferred, so the list reads as versions, not file formats.
    """
    by_key = {}
    for d in docs or []:
        url = (d.get("FilePath") or "").replace("\\", "/")
        if not url:
            continue
        stem = url.rsplit(".", 1)[0].lower()
        key = (d.get("GroupTypeID"), stem)
        fmt = (d.get("ApplicationDesc") or "").upper()
        cur = by_key.get(key)
        if cur is None or (fmt == "PDF" and cur["format"] != "PDF"):
            by_key[key] = {
                "group": " ".join((d.get("GroupTypeDesc") or "").split()),
                "group_id": d.get("GroupTypeID"),
                "format": fmt,
                "url": url,
                "_id": d.get("Id") or 0,
            }
    out = sorted(by_key.values(),
                 key=lambda x: (DOC_STAGE_ORDER.get(x["group_id"], 50), x["_id"]))
    for x in out:
        del x["_id"]
    return out


def classify(desc, title=""):
    s = desc or ""
    t = title or ""
    # Elections held inside the Knesset (Speaker, deputies, State Comptroller,
    # Knesset representatives to judicial-appointment committees…)
    if "בחירת" in t or "לבחירת" in t:
        return "election", "בחירות בכנסת"
    if "דין רציפות" in s:
        return "continuity", "דין רציפות"
    if "הסתייגות" in s:
        return "reservation", "הסתייגות"
    if "קריאה שלישית" in s:
        return "reading3", "קריאה שלישית"
    if "קריאה שנייה" in s:
        return "reading2", "קריאה שנייה"
    if "אי-אמון" in s or "אי אמון" in s:
        return "noconfidence", "אי־אמון"
    # There is NO standalone "approve in first reading" plenum vote (verified
    # in OData: 0 votes with 'בקריאה ראשונה'). First reading is decided by
    # referral votes: preliminary debate ends with referral to prepare for
    # first reading; the first-reading debate ends with referral to prepare
    # for readings 2-3. Keep them distinct — conflating them hides stage 1.
    if "להכנה לקריאה ראשונה" in s:
        return "pre1", "העברה להכנה לקריאה ראשונה"
    if "להכנה לקריאה שניה ושלישית" in s or "להכנה לקריאה שנייה ושלישית" in s:
        return "reading1", "קריאה ראשונה — העברה להכנה ל־2–3"
    if "קריאה ראשונה" in s:
        return "reading1", "קריאה ראשונה"
    if "להעביר" in s and "וועדה" in s:
        return "transfer", "העברה לוועדה"
    if "סדר-היום" in s or "סדר היום" in s:
        return "agenda", "הצעה לסדר"
    if "חקיקת משנה" in s or "תקנות" in s:
        return "secondary", "חקיקת משנה"
    return "other", (s[:38] or "אחר")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--knesset", type=int, default=25)
    args = ap.parse_args()
    kn = args.knesset
    print(f"=== Full build, Knesset {kn} ===", flush=True)

    print("statuses + committees...", flush=True)
    smap = {s["Id"]: s["Desc"] for s in fetch_all(
        "KNS_Status", {"$filter": "TypeID eq 2", "$select": "Id,Desc"}, label="statuses")}
    cmap = {c["Id"]: c["Name"] for c in fetch_all(
        "KNS_Committee", {"$filter": f"KnessetNum eq {kn}",
                          "$select": "Id,Name"}, label="committees")}

    print("all bills...", flush=True)
    bills = fetch_all("KNS_Bill", {
        "$filter": f"KnessetNum eq {kn}",
        "$select": ("Id,Name,TypeDesc,SubTypeID,SubTypeDesc,StatusID,CommitteeID,"
                    "PrivateNumber,Number,PublicationDate,SummaryLaw,LastUpdatedDate,"
                    "PostponementReasonDesc,IsContinuationBill,PublicationSeriesDesc,"
                    "MagazineNumber,PageNumber"),
        "$orderby": "Id",
    }, label="bills")
    bill_ids = [b["Id"] for b in bills]
    bill_set = set(bill_ids)

    print("initiators...", flush=True)
    inits = fetch_in("KNS_BillInitiator", "BillID", bill_ids, "",
                     "BillID,PersonID,IsInitiator,Ordinal", label="initiators")
    person_ids = sorted({i["PersonID"] for i in inits})
    print("persons...", flush=True)
    persons = fetch_in("KNS_Person", "Id", person_ids, "",
                       "Id,LastName,FirstName", label="persons")
    pname = {p["Id"]: f"{p.get('LastName','')} {p.get('FirstName','')}".strip()
             for p in persons}
    by_bill_init = defaultdict(list)
    for i in sorted(inits, key=lambda x: (not x["IsInitiator"], x["Ordinal"])):
        by_bill_init[i["BillID"]].append({
            "name": pname.get(i["PersonID"], str(i["PersonID"])),
            "initiator": bool(i["IsInitiator"]),
        })

    print("documents...", flush=True)
    docs = fetch_in("KNS_DocumentBill", "BillID", bill_ids, "",
                    "BillID,FilePath,ApplicationDesc,GroupTypeID,GroupTypeDesc,Id",
                    label="documents")
    by_bill_doc = defaultdict(list)
    for d in docs:
        by_bill_doc[d["BillID"]].append(d)
    doc_url = {bid: pick_doc_url(ds) for bid, ds in by_bill_doc.items()}
    bill_docs = {bid: build_docs(ds) for bid, ds in by_bill_doc.items()}
    bill_docs = {bid: ds for bid, ds in bill_docs.items() if ds}

    # Optional enrichment carried over from the earlier AI pipeline
    # (synopsis/topics/doc_url only — NOT vote data; vote linkage below is
    # built natively from the era vote headers + our own per-MK harvest).
    enrich = {}
    bp = DATA / "bills.json"
    if bp.exists():
        for b in json.loads(bp.read_text("utf-8")):
            if b.get("knesset_num") == kn and b.get("bill_id"):
                enrich.setdefault(b["bill_id"], []).append(b)

    # Tallies counted from our own per-MK harvest (same source the
    # harvester patches into k{N}_votes.json) — no legacy tallies.
    tally = {}
    vr_path = DATA / f"k{kn}_vote_results.json"
    if vr_path.exists():
        vr = json.loads(vr_path.read_text("utf-8"))
        for vid, rows in vr["votes"].items():
            c = {"for": 0, "against": 0, "abstain": 0}
            for _, code in rows:
                if code in (1, 2, 3):
                    c[["for", "against", "abstain"][code - 1]] += 1
            tally[int(vid)] = c
        print(f"tallies from {vr_path.name}: {len(tally)} votes", flush=True)

    print("all plenum votes of the era...", flush=True)
    votes = fetch_all("KNS_PlenumVote", {
        "$filter": f"VoteDateTime gt {ERA_START[kn]}",
        "$select": "Id,VoteDateTime,ItemID,VoteTitle,ForOptionDesc,IsNoConfidenceInGov,SessionID",
        "$orderby": "VoteDateTime",
    }, label="votes")
    READING = {"pre1": 0, "reading1": 1, "reading2": 2, "reading3": 3}
    votes_by_bill = defaultdict(list)
    out_votes = []
    for v in votes:
        kind, label = classify(v.get("ForOptionDesc"), v.get("VoteTitle"))
        if v.get("IsNoConfidenceInGov"):
            kind, label = "noconfidence", "אי־אמון"
        t = tally.get(v["Id"])
        rec = {
            "id": v["Id"], "date": (v.get("VoteDateTime") or "")[:10],
            "item_id": v.get("ItemID"),
            "in_bills": v.get("ItemID") in bill_set,
            "title": v.get("VoteTitle"), "desc": v.get("ForOptionDesc"),
            "kind": kind, "kind_label": label,
            "for": t["for"] if t else None,
            "against": t["against"] if t else None,
            "abstain": t["abstain"] if t else None,
        }
        out_votes.append(rec)
        if rec["item_id"] in bill_set and kind in READING:
            votes_by_bill[rec["item_id"]].append({
                "vote_id": rec["id"], "date": rec["date"],
                "reading": READING[kind],
                "for": rec["for"], "against": rec["against"],
                "abstain": rec["abstain"],
                "accepted": (1 if rec["for"] > rec["against"] else 0)
                if rec["for"] is not None and rec["against"] is not None else None,
            })

    out_bills = []
    for b in bills:
        kb = enrich.get(b["Id"], [])
        out_bills.append({
            "id": b["Id"], "name": b["Name"], "type": b.get("SubTypeDesc"),
            "status_id": b.get("StatusID"), "status": smap.get(b.get("StatusID")),
            "committee_id": b.get("CommitteeID"),
            "committee": cmap.get(b.get("CommitteeID")),
            "private_number": b.get("PrivateNumber"), "number": b.get("Number"),
            "publication_date": (b.get("PublicationDate") or "")[:10] or None,
            "publication_series": b.get("PublicationSeriesDesc") or None,
            "magazine": b.get("MagazineNumber"),
            "page": b.get("PageNumber"),
            "postponement_reason": b.get("PostponementReasonDesc") or None,
            "is_continuation": bool(b.get("IsContinuationBill")) or None,
            "summary": b.get("SummaryLaw") or None,
            "updated": (b.get("LastUpdatedDate") or "")[:10] or None,
            "initiators": by_bill_init.get(b["Id"], []),
            "synopsis": next((x["synopsis"] for x in kb if x.get("synopsis")), None),
            "topics": next((x["topics"] for x in kb if x.get("topics")), None),
            # Official נוסח from KNS_DocumentBill; the legacy pipeline's
            # doc_url (vote-linked bills only) is just a fallback now.
            "doc_url": doc_url.get(b["Id"]) or next(
                ((x["doc_url"] or "").replace("\\", "/") for x in kb
                 if x.get("doc_url")), None),
            "votes": sorted(votes_by_bill.get(b["Id"], []),
                            key=lambda x: x["date"] or ""),
        })

    pb = DATA / f"k{kn}_bills.json"
    pv = DATA / f"k{kn}_votes.json"
    pd_ = DATA / f"k{kn}_bill_docs.json"
    pb.write_text(json.dumps(out_bills, ensure_ascii=False), "utf-8")
    pv.write_text(json.dumps(out_votes, ensure_ascii=False), "utf-8")
    pd_.write_text(json.dumps(bill_docs, ensure_ascii=False), "utf-8")
    print(f"wrote {pd_} ({pd_.stat().st_size/1e6:.1f} MB, "
          f"{len(bill_docs)} bills with documents, "
          f"{sum(len(v) for v in bill_docs.values())} documents)")
    from collections import Counter
    print(f"\nwrote {pb} ({pb.stat().st_size/1e6:.1f} MB, {len(out_bills)} bills, "
          f"{sum(1 for b in out_bills if b['initiators'])} with initiators, "
          f"{sum(1 for b in out_bills if b['doc_url'])} with doc_url, "
          f"{sum(1 for b in out_bills if b['votes'])} with known votes)")
    print(f"wrote {pv} ({pv.stat().st_size/1e6:.1f} MB, {len(out_votes)} votes)")
    print("vote kinds:", Counter(v["kind"] for v in out_votes))
    print("bills by status (top):", Counter(b["status"] for b in out_bills).most_common(8))


if __name__ == "__main__":
    main()
