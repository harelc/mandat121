# מצביע ומשפיע

A Hebrew quiz app that matches users to Knesset members based on their votes on real bills from the 25th Knesset.

**Live demo**: serve locally with `python -m http.server 8080`

## How it works

1. User sees 10 bill synopses (AI-generated, in plain Hebrew) from diverse topics
2. User votes for/against/abstain on each
3. App compares to actual MK votes and shows closest/furthest MKs and factions

## Project structure

```
index.html              — Single-page quiz app (vanilla JS/CSS)
data/
  bills.json            — All bills with votes, synopses, topics
  synopses.json         — Cache of AI-generated bill synopses
  topics.json           — Cache of topic classifications
  mk_photos.json        — MK name → photo path mapping
  docs/                 — Downloaded bill documents (DOCX/PDF, gitignored)
  photos/               — Downloaded MK photos (gitignored)
```

## Data pipeline

### Quick start

```bash
# Set up
uv venv && uv pip install pdfplumber anthropic

# Full build (fetch + enrich) — takes ~15 minutes, needs Claude API
.venv/bin/python build_data.py

# Or enrich only (skip vote crawling, just regenerate synopses/topics)
.venv/bin/python build_data.py --enrich

# Serve
python -m http.server 8080
```

### Scripts (run order matters)

| Script | What it does | Output | API calls |
|--------|-------------|--------|-----------|
| `fetch_data.py` | Crawls K25 votes from undocumented Knesset REST API, matches bills to OData for doc URLs | `data/bills.json` | Knesset REST + OData |
| `generate_synopses.py` | Downloads bill docs (DOCX/PDF), extracts text, generates catchy Hebrew synopses | `data/synopses.json` → merges into `bills.json` | Claude Haiku |
| `generate_topics.py` | Classifies each bill into 3-5 topics from a taxonomy of 15 | `data/topics.json` → merges into `bills.json` | Claude Haiku |
| `fetch_photos.py` | Searches Hebrew Wikipedia for MK photos, downloads thumbnails | `data/mk_photos.json` + `data/photos/` | Wikipedia API |
| `build_data.py` | Orchestrates all scripts in order, does final merge | Everything | All of the above |

### Claude API access

Scripts look for API key in this order:
1. `ANTHROPIC_API_KEY` environment variable
2. macOS Keychain ("Claude Code" service) — auto-populated by `claude /login`

### Data sources

| Source | What | Bot protection |
|--------|------|---------------|
| `knesset.gov.il/WebSiteApi/knessetapi/Votes/GetVoteDetails/{id}` | Per-MK vote breakdowns (K25) | None |
| `knesset.gov.il/Odata/ParliamentInfo.svc/` | Bill metadata, doc URLs, MK data | None |
| `fs.knesset.gov.il` | Bill document files (PDF/DOCX) | None |
| `he.wikipedia.org/w/api.php` | MK photos (pageimages) | None |
| `main.knesset.gov.il` | Bill/vote pages (linked from quiz) | Radware (works in browser) |

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
  "synopsis": "AI-generated catchy Hebrew summary",
  "topics": ["ביטחון ומלחמה", "זכויות אזרח"],
  "doc_url": "https://fs.knesset.gov.il/25/law/...",
  "mk_votes": [
    {"mk_name": "שם חבר כנסת", "result": 1, "faction": "שם סיעה"}
  ]
}
```

### Topic taxonomy (15 topics)

ביטחון ומלחמה · כלכלה ותקציב · חינוך · בריאות · משפט ועונשין · דת ומדינה · זכויות אזרח · רווחה וביטוח לאומי · תחבורה ותשתיות · סביבה ואקלים · עבודה ותעסוקה · ממשל ודמוקרטיה · חברה ומיעוטים · דיור ונדל"ן · טכנולוגיה ומידע

## Tech stack

- **Frontend**: Single HTML file, vanilla JS/CSS, Heebo font
- **Data generation**: Python 3.11+, pdfplumber, anthropic SDK
- **Package management**: uv + pyproject.toml
- **Design**: Israeli flag palette (blue `#0038b8`, off-white `#fafaf8`)

## License

CC BY-NC-SA 4.0 — © הראל קין
