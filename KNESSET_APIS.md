# Knesset Data APIs — Field Guide

> **Last verified**: February 18, 2026

The Israeli Knesset has three layers of APIs, each with different levels of documentation, coverage, and protection. This document maps them all.

---

## Layer 1: Official OData v3 APIs (Documented)

Standard OData v3 services. No auth required. Accessible from any HTTP client.

### ParliamentInfo Service — WORKING, CURRENT
```
https://knesset.gov.il/Odata/ParliamentInfo.svc/
```

- **Schema**: `https://knesset.gov.il/Odata/ParliamentInfo.svc/$metadata`
- **Coverage**: Knesset 16–25 (current)
- **48 entity sets** including bills, documents, MKs, committees, plenary sessions
- **Key entities for bills**:
  - `KNS_Bill` — bill metadata (BillID, Name, KnessetNum, SummaryLaw)
  - `KNS_DocumentBill` — bill text files (BillID → FilePath on `fs.knesset.gov.il`)
  - `KNS_BillInitiator` — who proposed each bill
  - `KNS_PlmSessionItem` — plenary agenda items
- **Pagination**: `$top`, `$skip`, max ~1000 per page
- **Filtering**: Standard OData `$filter` with `substringof()`, `eq`, `ge`, etc.
- **No bot protection** on the API endpoint itself

**Example**:
```
GET /KNS_Bill?$format=json&$filter=KnessetNum eq 25&$top=5&$orderby=BillID desc
```

### Votes OData Service — WORKING BUT FROZEN AT KNESSET 24
```
https://knesset.gov.il/Odata/Votes.svc/
```

- **Schema**: `https://knesset.gov.il/Odata/Votes.svc/$metadata`
- **Coverage**: Knesset 16–24 only (last record: July 13, 2021, vote_id 34525)
- **4 entity sets**:
  - `View_vote_rslts_hdr_Approved` — vote headers with tallies
  - `vote_rslts_kmmbr_shadow` — per-MK vote records
  - `View_Vote_MK_Individual` — MK identity lookup
  - `vote_result_type` — result code descriptions
- **The pipeline feeding this service was never updated for Knesset 25**
- The service responds correctly — the data behind it is simply stale

---

## Layer 2: Undocumented REST API — THE KEY DISCOVERY

This is what the Knesset website itself uses internally. It is the **only public source for Knesset 25 vote data**.

### Vote Details Endpoint
```
GET https://knesset.gov.il/WebSiteApi/knessetapi/Votes/GetVoteDetails/{voteId}
```

- **Coverage**: All knessets including Knesset 25 through today
- **Auth**: None required
- **CORS**: `Access-Control-Allow-Origin: *` (works from browsers)
- **Bot protection**: None (unlike the website it powers)
- **Rate limiting**: None observed (we crawled hundreds of IDs with 10 concurrent requests)

**Response structure** (JSON):
```json
{
  "VoteHeader": [{
    "VoteId": 45200,
    "VoteDateStr": "5.2.2026",
    "VoteTimeStr": "13:45",
    "ItemTitle": "הצעת חוק ...",
    "FK_ItemID": 2226985,
    "FK_Knesset": 25,
    "SessionNumber": 179,
    "IsForAccepted": true,
    "AcceptedText": "ההצעה התקבלה",
    "Decision": "...",
    "ChairmanName": "אמיר אוחנה"
  }],
  "VoteCounters": [
    {"Title": "בעד", "countOfResult": 63, "ColorName": "#507282"},
    {"Title": "נגד", "countOfResult": 57, "ColorName": "#9F2214"},
    {"Title": "נמנע", "countOfResult": 2},
    {"Title": "נוכח ולא הצביע", "countOfResult": 1}
  ],
  "VoteDetails": [
    {
      "MkName": "אבוטבול משה",
      "FactionName": "התאחדות הספרדים...",
      "VoteResultId": 7,
      "Title": "בעד"
    }
  ],
  "NextAndPrevVotes": [{"NextVote": 45201, "PrevVote": 45199}]
}
```

**Key fields**:
- `VoteHeader[0].FK_Knesset` — filter by knesset number
- `VoteHeader[0].ItemTitle` — bill/item name
- `VoteHeader[0].FK_ItemID` — links to `KNS_PlmSessionItem.ItemID` in OData
- `VoteHeader[0].IsForAccepted` — boolean, whether the vote passed
- `VoteCounters[].Title` — "בעד" (for), "נגד" (against), "נמנע" (abstain), "נוכח ולא הצביע" (present, didn't vote)
- `VoteDetails[].MkName` — MK name
- `VoteDetails[].FactionName` — party/faction (full official name)
- `VoteDetails[].Title` — vote direction
- `NextAndPrevVotes[0].NextVote` / `.PrevVote` — for sequential crawling

**Vote ID ranges** (approximate, as of Feb 2026):
| Range | Knesset | Period |
|-------|---------|--------|
| 34526–35000 | 25 | Late 2022 |
| 40000–41000 | 25 | Early 2024 |
| 41000–42000 | 25 | Mid 2024 |
| 42000–44000 | 25 | Late 2024 |
| 44000–45000 | 25 | 2025 |
| 45000–45250 | 25 | Jan-Feb 2026 |

**Caveat**: IDs are NOT purely sequential within one knesset. Some IDs in the range return data from older knessets (e.g., 43000 = Knesset 19). Always check `FK_Knesset` in the response.

**Crawling strategy**: Start from a known vote ID and use `NextVote`/`PrevVote` to traverse, or iterate over a range and skip empty/wrong-knesset responses.

---

## Layer 3: The Website (Protected, Not Scrapable)

### Main Website
```
https://main.knesset.gov.il/
```

- **Vote pages**: `/activity/plenum/votes/pages/vote.aspx?voteid=XXXXX`
- **Smart Protocol**: `/apps/smartprotocol/session/{PlenumSessionID}/{ItemID}`
- **Protected by**: Radware Bot Manager
- **What you get**: JavaScript challenge only (`window.rbzns = {seed: "...", ...}; winsocks()`)
- **Requires**: Headless browser (Playwright/Selenium) to execute the JS challenge
- Has Knesset 25 data but is not practically scrapable without browser automation

### Bill Document Files — OPEN, NO PROTECTION
```
https://fs.knesset.gov.il/{KnessetNum}/law/{filename}.pdf
```

- Direct file downloads (PDF, DOC, DOCX)
- No auth, no bot protection
- Linked from `KNS_DocumentBill.FilePath` in the OData API
- Naming: `{KnessetNum}_ls_{type}_{id}.{ext}` or similar

---

## Third-Party Data Sources

### Open Knesset (hasadna) — CSV dumps, frozen at K24
```
https://production.oknesset.org/pipelines/data/
```

| Path | Content | Updated | Coverage |
|------|---------|---------|----------|
| `votes/view_vote_rslts_hdr_approved_extra/*.csv` | Vote headers (~12 MB) | Daily | K16–K24 |
| `votes/vote_rslts_kmmbr_shadow_extra/*.csv` | Per-MK votes (~99 MB) | Daily | K16–K24 |
| `members/mk_individual/` | MK profiles | 2023 | All |
| `committees/` | Committee data | Daily | Current |

**The vote data stops at K24** because the pipelines sourced from the OData Votes API which is frozen. The project is migrating to Airflow but hasn't ported the votes pipeline yet.

- **GitHub**: [hasadna/knesset-data-pipelines](https://github.com/hasadna/knesset-data-pipelines)
- **Redash**: `https://redash.hasadna.org.il/` (Gmail login, data source "next-oknesset")
- **Python client**: [hasadna/knesset-data-python](https://github.com/hasadna/knesset-data-python) (includes Reblaze bypass)

### data.gov.il — Misleading
```
https://data.gov.il/api/3/action/package_show?id=votes-knesset
```

- Dataset labeled "votes" is actually **election ballot data** (which party each settlement voted for), NOT parliamentary votes
- The "הצבעות חברי הכנסת במליאה" (plenum votes) dataset exists but has **0 resources**
- CKAN API works but there's nothing useful for our purpose

---

## The Data Model Gap

There is **NO direct foreign key** between bills and votes in any API:

```
KNS_Bill.BillID  ──(no link)──  vote_id / sess_item_id
```

The connection must be made through:
1. **Name matching**: `KNS_Bill.Name` ↔ `VoteHeader.ItemTitle` or `sess_item_dscr`
2. **Session items**: `KNS_PlmSessionItem.ItemID` = `VoteHeader.FK_ItemID` = `sess_item_id`, then match PlmSessionItem.Name to KNS_Bill.Name

---

## Summary

| Source | K25 Data | Auth | Bot Protection | Documented |
|--------|----------|------|----------------|------------|
| OData ParliamentInfo | Yes | None | None | Yes |
| OData Votes | **No (K24 max)** | None | None | Yes |
| WebSiteApi REST | **Yes (current)** | None | None | **No** |
| main.knesset.gov.il | Yes | None | **Radware** | N/A |
| fs.knesset.gov.il | Files only | None | None | No |
| Open Knesset CSVs | **No (K24 max)** | None | None | Partial |
| data.gov.il | **Wrong data** | None | Radware | Yes |
