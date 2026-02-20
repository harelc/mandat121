# ביע פיע

A Hebrew quiz app that matches you to Knesset members based on your votes on real bills. Vote on actual legislation, then discover which MKs and factions align with your views.

**Live demo**: serve locally with `python -m http.server 8080`

## What it does

1. Presents real bill synopses (AI-generated, in plain Hebrew) from diverse topics
2. You vote for/against/abstain on each bill
3. App compares your votes to actual MK voting records
4. Shows your closest and furthest MKs and factions, with an animated plenum visualization
5. Drill into any MK's full voting record, Wikipedia bio, and bill-by-bill agreement breakdown

## Data coverage

| Knesset | Period | Source | Bills |
|---------|--------|--------|-------|
| K25 | Nov 2022 – Feb 2026 | Knesset OData v4 API | ~1,086 |
| K24 | Jun 2021 – Jun 2022 | Knesset OData v4 API | ~103 |
| K23 | Mar 2020 – Jun 2021 | Knesset OData v4 API | ~107 |

Bills include two types of plenum votes:
- **3rd reading** (`vote_reading=3`): ~696 bills — final passage votes, mostly accepted
- **Pre-first-reading** (`vote_reading=0`, דיון מוקדם): ~600 bills — early-stage votes, mostly rejected opposition bills. The Knesset API has **1,633** such votes total; the fetch script is capped at 600 newest (`MAX_NEW_BILLS`), so more can be added by increasing this limit.

This mix gives the quiz both passed laws and rejected proposals, making the voting experience more balanced.

All 120 current K25 MKs are included with photos (sourced from Wikipedia and the Knesset website) and linked to their Hebrew Wikipedia articles.

## Project structure

```
index.html                 Single-page app (vanilla JS/CSS, ~70KB)
fetch_all_bills.py         Fetches 2nd/3rd reading bills from Knesset OData v4 API (K23-25)
fetch_prefirst_bills.py    Fetches pre-first-reading (דיון מוקדם) bills from OData v4
fetch_past_bills.py        Fetches K23/K24 bills from Hasadna CSV dumps (legacy)
fetch_photos.py            Downloads MK photos from Wikipedia
generate_synopses.py       Generates Hebrew bill synopses using Claude API (parallel)
build_data.py              Master build script — runs all steps in order
KNESSET_APIS.md            Field guide to all Knesset data APIs
data/
  bills.json               All bills with votes, synopses (~1,296 bills)
  mk_photos.json           MK name -> {photo_url, source, faction, display_name} (120 MKs)
  mk_wiki.json             MK name -> Wikipedia article title mapping (120 MKs)
  docs/                    Downloaded bill documents (DOCX/PDF, gitignored)
  photos/                  Downloaded MK photos (74 local JPEGs)
```

## Data pipeline

### Quick start

```bash
# Set up
uv venv && uv pip install pdfplumber anthropic

# Full build (fetch + enrich) — takes ~15 min, needs Claude API key
.venv/bin/python build_data.py

# Enrich only (skip vote fetching, regenerate synopses/topics)
.venv/bin/python build_data.py --enrich

# Past knesset data (optional, droppable)
.venv/bin/python fetch_past_bills.py          # fetch K23+K24
.venv/bin/python fetch_past_bills.py --merge  # merge into bills.json
.venv/bin/python fetch_past_bills.py --drop   # remove past data

# Serve
python -m http.server 8080
```

### Scripts

| Script | What it does | Output |
|--------|-------------|--------|
| `fetch_all_bills.py` | Fetches 2nd/3rd reading bills from Knesset OData v4 (K23-25) | `data/bills.json` |
| `fetch_prefirst_bills.py` | Fetches pre-first-reading votes from OData v4 (capped at 600, 1,633 available) | merges into `data/bills.json` |
| `fetch_past_bills.py` | Downloads K23/K24 votes from Hasadna CSVs (legacy, superseded by OData v4) | `data/bills_past.json` |
| `generate_synopses.py` | Downloads bill docs, extracts text, generates Hebrew synopses via Claude (10 parallel workers) | updates `data/bills.json` |
| `fetch_photos.py` | Finds MK photos on Hebrew Wikipedia, downloads thumbnails | `data/mk_photos.json` + `data/photos/` |
| `build_data.py` | Orchestrates all scripts in order, does final merge | Everything |

### Claude API access

Synopsis and topic generation require a Claude API key:
1. `ANTHROPIC_API_KEY` environment variable
2. macOS Keychain ("Claude Code" service) — auto-populated by `claude /login`

## Data sources & known issues

### Sources

| Source | What | Coverage | Bot protection |
|--------|------|----------|---------------|
| Knesset OData v4 (`knesset.gov.il/OdataV4/ParliamentInfo/`) | Plenum votes, per-MK results, bill metadata, doc URLs | K16–K25 | None |
| Knesset file server (`fs.knesset.gov.il`) | Bill documents (PDF/DOCX) | All | None |
| Hebrew Wikipedia | MK photos and bios | Current | None |
| Knesset website (`main.knesset.gov.il`) | Bill/vote web pages | All | **Radware** (474 errors from scripts) |

### Known issues

- **Radware bot protection** on `main.knesset.gov.il` returns HTTP 474 when accessed from scripts. The OData and file server APIs are unaffected.
- **OData v4 quirks**: `KNS_PlenumVote` has no `ForCount`/`AgainstCount` fields — vote totals must be computed from `KNS_PlenumVoteResult`. Pagination requires explicit `$skip`/`$top` — `nextLink` can hang on filtered queries. The `contains()` filter works with Hebrew text for server-side filtering.
- **MK name normalization**: Some MKs have variant spellings (e.g. "גנץ בנימין" vs "גנץ בני"). Aliases are maintained in the fetch scripts.
- **Pre-first-reading cap**: 1,633 pre-first-reading votes exist in the API (K23-25), but `fetch_prefirst_bills.py` caps at 600 newest to keep the dataset balanced. Increase `MAX_NEW_BILLS` to fetch more.

### bills.json schema

```json
{
  "id": "2226483",
  "name": "הצעת חוק ...",
  "vote_date": "2026-02-18",
  "vote_id": 45239,
  "bill_id": 2240064,
  "is_accepted": 0,
  "total_for": 37,
  "total_against": 53,
  "total_abstain": 0,
  "bill_type": "פרטית",
  "synopsis": "AI-generated catchy Hebrew summary",
  "topics": ["ביטחון ומלחמה", "זכויות אזרח"],
  "doc_url": "https://fs.knesset.gov.il/25/law/...",
  "knesset_num": 25,
  "vote_reading": 0,
  "mk_votes": [
    {"mk_name": "שם חבר כנסת", "result": 1, "faction": "שם סיעה"}
  ]
}
```

`result`: 1 = for (בעד), 2 = against (נגד), 3 = abstain (נמנע)

`bill_type`: ממשלתית (government), פרטית (private), ועדה (committee)

`vote_reading`: 0 = pre-first-reading (דיון מוקדם), 2 = second reading, 3 = third reading

### Topic taxonomy (15 topics)

ביטחון ומלחמה · כלכלה ותקציב · חינוך · בריאות · משפט ועונשין · דת ומדינה · זכויות אזרח · רווחה וביטוח לאומי · תחבורה ותשתיות · סביבה ואקלים · עבודה ותעסוקה · ממשל ודמוקרטיה · חברה ומיעוטים · דיור ונדל"ן · טכנולוגיה ומידע

## Tech stack

- **Frontend**: Single HTML file, vanilla JS/CSS, Heebo + Rubik fonts, SVG plenum visualization
- **Data generation**: Python 3.11+, pdfplumber, anthropic SDK
- **Package management**: uv + pyproject.toml
- **Hosting**: Static files only — no server required

## License

CC BY-NC-SA 4.0 — Harel Cain
