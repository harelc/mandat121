# Knesset OData v4 API — Field Guide

> **Last verified**: November 9, 2026 (against the official manual `KnessetOdataManual.docx` + live probes)

## Nov 2026 corrections & findings (read first)

- **`$expand` DOES work** (earlier version of this guide said it doesn't). Verified live: `KNS_Bill?$filter=Id eq 2216582&$expand=KNS_BillInitiator,KNS_Status,KNS_DocumentBill` returns all three navigation properties. The official manual's examples rely on `$expand` throughout. This removes most batch-join plumbing.
- **Knesset 26: not published yet** (2026-11-09). `KNS_Bill` / `KNS_PersonToPosition` / `KNS_Faction` / `KNS_Committee` / sessions for `KnessetNum eq 26` all return 0; `KNS_KnessetDates IsCurrent` still = K25. Run `python3 probe_knesset.py` to re-check (exit 2 = not yet).
- **K25 is still active, plenum is not the whole story**: last plenum session was 2026-07-28 (#418), but committee sessions ran to 2026-11-04 and 806 K25 bills had `LastUpdatedDate` after 2026-06-24. A votes-only feed goes silent for months while legislative work continues.
- **There is no "first reading" acceptance vote.** Verified live (2026-11-09): `KNS_PlenumVote` for the K25 era (7,557 votes, all harvested) contains **zero** rows with `בקריאה ראשונה` in `ForOptionDesc`. First reading is decided by referral votes: preliminary debate ends with `להעביר… להכנה לקריאה ראשונה` (1,199 votes) and the first-reading debate ends with `להעביר… להכנה לקריאה שניה ושלישית` (595). Only readings 2–3 have acceptance votes (`לקבל בקריאה שנייה` 1,216; `…שלישית` 556). Do not classify by searching for "קריאה ראשונה" acceptance — map the two referral families to pre-1 and first-reading instead.
- **Plenum votes are mostly NOT bill readings.** Of 481 votes 2026-06-24→07-28: 258 were `לקבל את ההסתייגות` (reservation/amendment votes in 2nd reading), 102 2nd-reading, 39 pre-1st, 38 3rd-reading, rest no-confidence / transfer-to-committee / agenda motions. Any serious view needs a vote-type taxonomy over `ForOptionDesc`, not just reading detection.
- **Bill lifecycle is recoverable**: `KNS_Bill.StatusID` → `KNS_Status` (36 bill statuses, TypeID=2; `OrderTransition` is null — order stages by session dates, not status codes). Per-stage history = `StatusID` snapshots on `KNS_PlmSessionItem` / `KNS_CmtSessionItem` rows for the bill, joined to session dates.
- **Join MKs by `PersonID`, never by name.** `KNS_PlenumVoteResult.MkId` = `KNS_PersonToPosition.PersonID` = `KNS_BillInitiator.PersonID` = `KNS_Person.Id`. The name-matching + alias table in the current fetch scripts is the main fragility and will break on a new Knesset's roster. `KNS_PersonToPosition` (PositionID=54, with Start/FinishDate) also gives faction *at time of vote*, including mid-term switches/splits.
- **`LastUpdatedDate` watermark caveat**: unfiltered, it surfaces corrections to old laws (e.g. a K18 bill updated 2026-11-03). Always watermark per-`KnessetNum`.
- **Unused-by-biapia entities that matter for a full legislative view**: `KNS_BillInitiator` (+`KNS_BillHistoryInitiator`), `KNS_BillName` (name per reading), `KNS_BillUnion`/`KNS_BillSplit`, `KNS_Committee`/`KNS_CommitteeSession`/`KNS_CmtSessionItem`/`KNS_DocumentCommitteeSession`/`KNS_BroadcastCommitteSession`, `KNS_PlenumSession`/`KNS_PlmSessionItem`/`KNS_DocumentPlenumSession` (incl. דברי הכנסת, GroupTypeID=28), `KNS_IsraelLaw*` + `KNS_IsraelLawBinding` (bill → enacted law in ספר החוקים; `KNS_Bill.PublicationDate/MagazineNumber/PageNumber`), `KNS_Query`, `KNS_Agenda`, `KNS_Faction`.
- **Known manual/API quirks**: manual itself flags `KNS_PlmSessionItem` `$orderby=Ordinal` as broken; duplicate status descriptions exist (e.g. several `לאישור פיצול במליאה` codes); government bills have no `KNS_BillInitiator` rows; `SummaryLaw` exists only after 3rd reading.

The official Knesset OData v4 API is the **only source needed** for bills, votes, MK data, and documents across all knessets. No scraping, no undocumented endpoints, no third-party mirrors.

---

## Base URL

```
https://knesset.gov.il/OdataV4/ParliamentInfo/
```

- No auth required
- No bot protection
- Standard OData v4 query syntax (`$filter`, `$select`, `$orderby`, `$top`, `$count`)
- Default page size: **100** items
- Pagination: follow `@odata.nextLink` in response
- `$expand` is **NOT supported** (returns error)
- `in` operator works for batch queries: `$filter=Id in (1,2,3)`
- `contains()` works for string filtering: `contains(ForOptionDesc,'קריאה שלישית')`
- `$count=true` returns `@odata.count` in response

---

## Key Entities

### KNS_PlenumVote — Vote headers

| Field | Type | Description |
|-------|------|-------------|
| `Id` | int | **Primary key** (this is the "vote ID") |
| `VoteDateTime` | datetime | When the vote took place |
| `SessionID` | int | Plenary session ID |
| `ItemID` | int | **FK → KNS_Bill.Id** (direct link!) |
| `VoteTitle` | string | Bill/item name as voted on |
| `VoteSubject` | string | Vote subject category |
| `ForOptionDesc` | string | Description of the "for" option (e.g., "לקבל את הצעת החוק בקריאה שלישית") |
| `AgainstOptionDesc` | string | Description of the "against" option |
| `VoteStatusCode` | int | 7 = מפורסם (published) |
| `VoteMethodID` | int | 1 = אלקטרונית, 2 = שמית (both have per-MK rows), 3 = חשאית, 4 = הרמת ידיים עם מונים, 5 = הרמת ידיים ללא מונים (3–5 have **no** per-MK rows, ever) |
| `Ordinal` | int | Vote order within session |

**Filtering 2nd+3rd reading votes:**
```
KNS_PlenumVote?$filter=VoteDateTime gt 2020-03-01T00:00:00Z
  and (contains(ForOptionDesc,'קריאה שלישית')
  or contains(ForOptionDesc,'לקבל בקריאה שנייה'))
&$orderby=VoteDateTime desc
```

**ForOptionDesc patterns:**
- 3rd reading: `"לקבל את הצעת החוק בקריאה שלישית"`
- 2nd reading: `"לקבל בקריאה שנייה"`

**Vote counts (K23–25, as of Feb 2026):** 2266 total (1583 2nd reading, 683 3rd reading)

---

### KNS_PlenumVoteResult — Per-MK votes

| Field | Type | Description |
|-------|------|-------------|
| `Id` | int | Primary key |
| `VoteID` | int | **FK → KNS_PlenumVote.Id** |
| `MkId` | int | **FK → KNS_PersonToPosition.PersonID** |
| `ResultCode` | int | **7=בעד, 8=נגד, 9=נמנע** |
| `ResultDesc` | string | Hebrew description of result |
| `LastName` | string | MK last name |
| `FirstName` | string | MK first name |
| `VoteDate` | datetime | Vote date (same as parent vote) |
| `SessionID` | int | Session ID |
| `ItemID` | int | Item ID |

**No faction info** — must cross-reference `KNS_PersonToPosition` for faction.

**Batch fetch example:**
```
KNS_PlenumVoteResult?$filter=VoteID in (45214,45215,45205)
  &$select=VoteID,MkId,ResultCode,ResultDesc,LastName,FirstName
  &$top=1000
```

Typical vote has ~17–120 MK results.

---

### KNS_Bill — Bill metadata

| Field | Type | Description |
|-------|------|-------------|
| `Id` | int | **Primary key** (= PlenumVote.ItemID) |
| `KnessetNum` | int | Knesset number (1–25) |
| `Name` | string | Full bill name |
| `TypeID` | int | 2 = הצעת חוק |
| `SubTypeID` | int | **53=ממשלתית, 54=פרטית, 55=ועדה** |
| `SubTypeDesc` | string | Hebrew bill type |
| `SummaryLaw` | string | Bill summary (often null) |
| `StatusID` | int | Bill status code |
| `PublicationDate` | datetime | Publication date |
| `PrivateNumber` | string | Private bill number (if applicable) |
| `CommitteeID` | int | Committee that handled the bill |

**Batch fetch:**
```
KNS_Bill?$filter=Id in (1046353,2227749)
  &$select=Id,Name,KnessetNum,SubTypeID,SubTypeDesc,SummaryLaw
```

---

### KNS_DocumentBill — Bill documents

| Field | Type | Description |
|-------|------|-------------|
| `Id` | int | Primary key |
| `BillID` | int | **FK → KNS_Bill.Id** |
| `GroupTypeID` | int | Document type (15 = קטע מדברי הכנסת) |
| `GroupTypeDesc` | string | Document type description |
| `ApplicationDesc` | string | File format (DOC, PDF, etc.) |
| `FilePath` | string | Full URL on `fs.knesset.gov.il` |

**Get latest document:**
```
KNS_DocumentBill?$filter=BillID eq 1046353
  &$orderby=Id desc&$top=1&$select=FilePath
```

File URLs: `https://fs.knesset.gov.il/{KnessetNum}/law/{filename}.{ext}` — open, no auth.

---

### KNS_PersonToPosition — MK faction lookup

| Field | Type | Description |
|-------|------|-------------|
| `Id` | int | Primary key |
| `PersonID` | int | MK ID (= PlenumVoteResult.MkId) |
| `PositionID` | int | **54 = חבר כנסת** |
| `KnessetNum` | int | Knesset number |
| `FactionID` | int | Faction ID |
| `FactionName` | string | Full faction name |
| `IsCurrent` | bool | Currently serving |
| `StartDate` | datetime | Position start |
| `FinishDate` | datetime | Position end |

**Get all MK→faction mappings for K25:**
```
KNS_PersonToPosition?$filter=PositionID eq 54 and KnessetNum eq 25
  &$select=PersonID,FactionName,IsCurrent
```

**Current K25 members:** 120 (filter `IsCurrent eq true`)

---

## Key Relationships

```
KNS_PlenumVote.ItemID ──FK──→ KNS_Bill.Id        (direct! no fuzzy matching needed)
KNS_PlenumVoteResult.VoteID ──FK──→ KNS_PlenumVote.Id
KNS_PlenumVoteResult.MkId ──FK──→ KNS_PersonToPosition.PersonID
KNS_DocumentBill.BillID ──FK──→ KNS_Bill.Id
```

---

## Data Fetch Strategy (implemented in `fetch_all_bills.py`)

1. **Faction lookup**: `KNS_PersonToPosition` for K23/24/25 → `{(PersonID, KnessetNum): FactionName}`
2. **Votes**: All 2nd+3rd reading votes since 2020-03-01 via `KNS_PlenumVote` (paginated, ~2266 results)
3. **Vote results**: Batch fetch `KNS_PlenumVoteResult` using `VoteID in (...)` with batches of 10
4. **Bill metadata**: Batch fetch `KNS_Bill` using `Id in (...)` with batches of 20
5. **Documents**: Per-bill fetch `KNS_DocumentBill` (latest document per bill)
6. **Assembly**: Prefer 3rd reading over 2nd for same bill, compute totals from results, merge with existing synopses/topics

Rate limiting: 0.3s between API calls. Full fetch takes ~5–10 minutes.

---

## Bill Document Files — Direct Download

```
https://fs.knesset.gov.il/{KnessetNum}/law/{filename}.{ext}
```

- No auth, no bot protection
- PDF, DOC, DOCX formats
- URLs from `KNS_DocumentBill.FilePath`

---

## Deprecated / Not Needed

| Source | Why deprecated |
|--------|---------------|
| `knesset.gov.il/Odata/ParliamentInfo.svc/` (OData v3) | Superseded by v4 |
| `knesset.gov.il/Odata/Votes.svc/` (OData v3) | Frozen at K24, not updated |
| `knesset.gov.il/WebSiteApi/knessetapi/Votes/` | Undocumented REST, brute-force vote ID crawling — replaced by OData v4 filtered queries for per-MK rows. **Correction (2026-11-11):** `GET Votes/GetVoteDetails/<id>` on this API is live and is the *only* structured source for non-electronic votes — OData has no tally at all for them (no counts on `KNS_PlenumVote`, no rows in `KNS_PlenumVoteResult`). It returns `VoteCounters` (hand votes with counters; zero categories omitted), `DescreetVoteResults` (secret-ballot candidate counts) and `VoteHeader.IsForAccepted` (the verdict for uncounted hand votes). `build_knesset.py` uses it as a per-vote fallback only when the OData tally is missing (~85 era votes). |
| hasadna GCS CSV mirrors | Frozen at K24, sourced from stale OData v3 Votes |
| Fuzzy name matching for bill↔vote | Not needed — `PlenumVote.ItemID → Bill.Id` is a direct FK |
