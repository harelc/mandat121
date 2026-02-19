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
| K25 | Nov 2022 – Feb 2026 | Knesset undocumented REST API | ~225 |
| K24 | Apr – Jul 2021 | Hasadna public CSV dumps | ~4 |
| K23 | Mar 2020 – Mar 2021 | Hasadna public CSV dumps | ~110 |

**Note**: K24 data is sparse because the Hasadna pipeline stopped updating after July 2021. There is a gap in coverage from mid-2021 to late 2022 (K24's remaining term). K23 and K24 bills only include votes from MKs who also serve in K25.

All 120 current K25 MKs are included with photos (sourced from Wikipedia and the Knesset website) and linked to their Hebrew Wikipedia articles.

## Project structure

```
index.html                 Single-page app (vanilla JS/CSS, ~70KB)
build_data.py              Master build script — runs all steps in order
fetch_all_bills.py         Fetches K25 votes + bill catalog + doc URLs
fetch_past_bills.py        Fetches K23/K24 bills from Hasadna CSV dumps
fetch_photos.py            Downloads MK photos from Wikipedia
generate_synopses.py       Generates Hebrew bill synopses using Claude API
generate_topics.py         Classifies bills into topic taxonomy using Claude API
KNESSET_APIS.md            Field guide to all Knesset data APIs
data/
  bills.json               All bills with votes, synopses, topics (~340 bills)
  bills_past.json          Past-knesset bills before merge (droppable)
  bill_catalog.json        Full KNS_Bill catalog from Hasadna (~7K K25 entries)
  synopses.json            Cache of AI-generated bill synopses
  topics.json              Cache of topic classifications
  mk_photos.json           MK name -> photo URL/path mapping (120 MKs)
  mk_wiki.json             MK name -> Wikipedia article title mapping (120 MKs)
  docs/                    Downloaded bill documents (DOCX/PDF, gitignored)
  photos/                  Downloaded MK photos (gitignored)
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
| `fetch_all_bills.py` | Crawls K25 votes from Knesset REST API, matches to bill catalog for type/docs | `data/bills.json` |
| `fetch_past_bills.py` | Downloads K23/K24 votes from Hasadna CSVs, filters to K25 MKs only | `data/bills_past.json` |
| `generate_synopses.py` | Downloads bill docs, extracts text, generates Hebrew synopses via Claude | `data/synopses.json` |
| `generate_topics.py` | Classifies each bill into topics from a 15-topic taxonomy via Claude | `data/topics.json` |
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
| Knesset REST API (`WebSiteApi/knessetapi/Votes/`) | Per-MK vote breakdowns | K25 (current) | None |
| Knesset OData (`Odata/ParliamentInfo.svc/`) | Bill metadata, doc URLs | K16–K25 | None |
| Knesset file server (`fs.knesset.gov.il`) | Bill documents (PDF/DOCX) | All | None |
| Hasadna GCS (`storage.googleapis.com/knesset-data-pipelines/`) | Vote CSVs, bill catalog | K16–K24 | None |
| Hebrew Wikipedia | MK photos and bios | Current | None |
| Knesset website (`main.knesset.gov.il`) | Bill/vote web pages | All | **Radware** (474 errors from scripts) |

### Known issues

- **Radware bot protection** on `main.knesset.gov.il` returns HTTP 474 when accessed from scripts. The OData and REST APIs are unaffected. Bill document downloads from `fs.knesset.gov.il` sometimes also get blocked.
- **Knesset OData Votes service** (`Odata/Votes.svc/`) is frozen at K24 (last record: July 2021). The undocumented REST API is the only source for K25 vote data.
- **K24 data gap**: Hasadna's vote pipeline stopped updating after July 2021, so K24 coverage is only ~3 months. The actual K24 lasted until June 2022.
- **No direct bill-vote foreign key**: Bills and votes must be matched by name similarity (`SequenceMatcher`), which can miss or mismatch bills with similar names.
- **MK name format varies**: K25 REST API uses "last first", Hasadna CSVs use "first last". Matching uses `frozenset` of name parts.

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
  "knesset": 25,
  "mk_votes": [
    {"mk_name": "שם חבר כנסת", "result": 1, "faction": "שם סיעה"}
  ]
}
```

`result`: 1 = for (בעד), 2 = against (נגד), 3 = abstain (נמנע)

`bill_type`: ממשלתית (government), פרטית (private), ועדה (committee)

### Topic taxonomy (15 topics)

ביטחון ומלחמה · כלכלה ותקציב · חינוך · בריאות · משפט ועונשין · דת ומדינה · זכויות אזרח · רווחה וביטוח לאומי · תחבורה ותשתיות · סביבה ואקלים · עבודה ותעסוקה · ממשל ודמוקרטיה · חברה ומיעוטים · דיור ונדל"ן · טכנולוגיה ומידע

## Tech stack

- **Frontend**: Single HTML file, vanilla JS/CSS, Heebo + Rubik fonts, SVG plenum visualization
- **Data generation**: Python 3.11+, pdfplumber, anthropic SDK
- **Package management**: uv + pyproject.toml
- **Hosting**: Static files only — no server required

## License

CC BY-NC-SA 4.0 — Harel Cain
