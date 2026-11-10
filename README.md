# The 121st Mandate (המנדט ה־121)

Live citizen tracking of the Knesset: every bill, every vote, every MK — built
directly from the Knesset's official OData. 120 mandates are handed out on
election day; the 121st stays with the citizens, and works the whole term.

- **The app**: `index.html` at the repo root (single file, static data from `data/`)
- **Builders**: `build_*.py` (stdlib only) read the official OData and write snapshots to `data/`
- **Run locally**: `python3 -m http.server 8092`, then open `http://localhost:8092/`
- **License**: code under MIT, site content CC BY-NC-SA 4.0, third-party data (Knesset, Wikipedia, Wikidata) under its own terms — see `LICENSE`

## Documentation

This README is only the front door. The deeper docs are:

- `ROADMAP.md` — plan, current status, and roadmap
- `OPEN_KNESSET.md` — the design document
- `KNESSET_APIS.md` — the Knesset OData API field guide
- `thread/THREAD.md` — the launch thread (cards in `thread/cards/`)

## biapia

The Bia-Pia game now lives in the `biapia/` folder (with its own README and
builders) and is published as a separate site. Its deploy is assembled at
build time from the shared data in `data/` (see `biapia/build_site.sh` and
`biapia/netlify.toml`), and documented in `biapia/README.md`.
