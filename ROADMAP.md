# המנדט ה־121 — Roadmap

Decided 2026-11-10 (owner decisions in brackets). Brand: **המנדט ה־121**.
Deploy target: Netlify site `mandat121`. [new site, not rename]

Repo layout (since 2026-11-10): the **root is המנדט ה־121** — the app is
root `index.html` (renamed from `open.html`), builders `build_*.py`, data
in `data/`. The biapia game moved to **`biapia/`** (page + its builders +
README). The bia-pia Netlify site now builds with **base directory
`biapia`** (set via API 2026-11-10), where `biapia/netlify.toml` runs
`biapia/build_site.sh`: it assembles `biapia/dist/` from the game page
plus the game data files copied out of the shared root `data/` (single
source of truth — no duplication in git). Verified locally: dist
assembles to ~20MB and loads. This takes effect on the next push to
`main` — which, per the owner rule below, happens only on owner's say-so.

## Presence — resolved (2026-11-10)

There is **no presence entity in OData** (checked `$metadata`: 47 entity
sets, none attendance-related). But the human page the owner meant exists —
the MK-lobby app at `main.knesset.gov.il/apps/mklobby/main/mks-attendance` —
and it is powered by a scriptable JSON API on the `www` host (not the
Radware-walled `main` host):

```
GET https://www.knesset.gov.il/WebSiteApi/knessetapi/MkLobby/GetMkLobbyData120?lang=he
```

Per MK it returns `IsPresent` (in the building *right now*), plus official
`Email`, `Phone`, `Fax`, `Facebook`, `Twitter`, `Instegram`, `Youtube`,
`WebsiteUrl`, birth year. `build_mk_contacts.py` snapshots it into
`data/mk_contacts.json` (120 MKs; at first fetch: 119 emails, 74 Facebook,
66 X, 31 Instagram, 9 present). Caveats, enforced in the UI:

- `IsPresent` is a **live snapshot, not history**. No time series exists;
  we label it with the fetch time and never backfill.
- Only official, Knesset-published channels are shown (office email/phone,
  not personal data).

## Sprint 1 — DONE 2026-11-10 (A+B, owner's pick)

**A. MK analytics** (`build_analytics.py` → `data/k25_analytics.json`, new
📊 ניתוח tab + per-MK card in the MK page):

- Vote-correlation matrix: **all** MKs who served (149 at last build —
  120 seats + replacements), ordered by hierarchical clustering
  (average linkage on agreement distance) so it reads Jordan-style:
  blocs as squares on the diagonal, white lines at cluster boundaries.
  Measured at last build: 98.8% mean agreement inside clusters vs 8.9%
  between. (An earlier 36-MK cap was dropped — it hid the structure.)
- Close ties: closest/furthest pairs overall + per-MK allies/opponents
- Pair inspector: clicking any matrix cell lists the actual votes where
  that pair agreed (with a disagreed toggle) — e.g. a coalition↔opposition
  pair at 3% agreement has exactly 57 joint votes, each linked to its
  vote page. Computed live from `k25_vote_results.json`
- Rank by bills initiated (lead initiator) *and* rank by votes participated
  — bills first in the layout, per owner preference
- Honest coverage label: analytics run on however many of the 7,536 era
  votes have per-MK results harvested so far (7,181 at last build)

**B. Protocols** (`build_protocols_search.py`, committees tab):

- Global full-text-ish search across all 268 parsed committee protocols
  (~3M words): word/prefix match ranked by mentions, deep-links to session
- Word clouds: global (all protocols) + per-protocol (click a word to
  filter the reader)

**Also in sprint 1:** contact/social/presence card on every MK page
(`build_mk_contacts.py`); harvester pagination bug fixed in
`build_k25_vote_results.py` (service caps pages at 100 rows regardless of
`$top` — 1,008 votes were silently truncated at exactly 100 rows; they are
now queued for refetch).

## Queue — in priority order

### P0. Finish the data foundation (from 2026-11-09 late-night list)
1. ✅ DONE 2026-11-10: per-MK harvest complete — 7,451/7,557 era votes
   with results (the other 106 return zero result rows from OData itself),
   149 MKs, tallies patched into `k25_votes.json`.
2. ✅ DONE 2026-11-10: `build_k25_committee_members.py` verified —
   135 MKs, 87 committees, 0 unmatched (it had in fact completed earlier
   that night; committee chips on MK pages and member lists on committee
   pages now have data).
3. GitHub Actions: scaffold exists (`.github/workflows/knesset-daily.yml`,
   daily 04:30 UTC, probe-gated K26 build, commits `data/` back; new
   Sprint-1 builders added 2026-11-10). Still missing: Netlify deploy hook
   step after the data commit, and a first manual `workflow_dispatch` run
   to prove it end-to-end. Note: Netlify deploys here are currently manual
   (`netlify deploy --prod --dir=<staging>`, see Deploy) — the workflow
   does not yet trigger one.
4. Party tab icon: replace 🚩/yellow-ribbon-adjacent imagery — the yellow
   ribbon is associated with the Gaza hostages; do not use it for parties.

### P1. Graph analysis of MKs
Network graph over the analytics pairs (edge = agreement ≥ threshold,
faction colours, click → MK). Data is ready (`k25_analytics.json` pairs);
needs a layout (force-directed in-page, no backend).

### P1. Committee hub remake
Especially the permanent committees (ועדות קבועות): richer hub — members,
chair, recent sessions, protocol search per committee, bills in progress,
follow button prominence. Builds on committees + protocols + members (P0.2).

### P1. OData gaps — what we are not using yet
From the live `$metadata` inventory, unused entities worth harvesting:
`KNS_Query` (שאילתות) + `KNS_DocumentQuery`, `KNS_Agenda` (הצעות לסדר),
`KNS_IsraelLaw*` (bill → enacted law, completes Bill Journey),
`KNS_BillUnion`/`KNS_BillSplit`/`KNS_BillName`, `KNS_SecondaryLaw*`
(חקיקת משנה), `KNS_DocumentPlenumSession` (דברי הכנסת — *plenary*
protocols, searchable like committee ones), `KNS_BroadcastCommitteSession`
(session video), `V_Lobbyist`/`V_LobbyistsClient` (who lobbies whom),
`KNS_GovMinistry`, `KNS_JointCommittee`.

### P2. Rich topic / keyword topology
Today's topics are a small closed typology (owner noted: starring a topic
is low-value while it's small). Replace/augment with keywords extracted
from bill texts + protocol word data (already indexed) → topic pages with
bills, MKs, votes, protocols per keyword; only then make topics followable.

### P2. Future schedule + follow-anything
Upcoming plenum/committee sessions (`KNS_PlenumSession`/
`KNS_CommitteeSession` future-dated) with follow per committee/MK/session.
Follows stay local (localStorage) for now; "most followed" is impossible
until a backend exists — see Deferred.

### P2. Knesset explainer + Wikipedia abstracts
Explainer pages (how a bill becomes law, what a committee does…) —
partly present in Bill Journey copy; formalise.
✅ DONE 2026-11-10 (abstracts + X discovery): `build_mk_wiki.py` →
`data/mk_wiki_abstracts.json` — Hebrew Wikipedia lead abstracts for
120/120 mapped MKs (CC BY-SA, attributed + linked in a 📖 card on the MK
page), plus Wikidata P2002 X handles: 83 on Wikidata, **33 of them not
published by the Knesset lobby** (e.g. @itamarbengvir, @gantzbe,
@NirBarkat) — shown in the contact card only as a visibly separate
"מויקינתונים" chip, never merged into `mk_contacts.json`. 2 mismatches
vs the Knesset handle are reported by the builder (official wins in UI).
Still open: abstracts for the ~29 former MKs with no `mk_wiki.json`
mapping (needs new title mappings / search-based resolution), and the
explainer pages themselves.

### P2. Popular votes
"Votes everyone marked / closest / most rebel-heavy" on the votes tab —
computable locally (closeness + my-vote counts are local; global popularity
needs the deferred backend).

### Deferred — needs a backend [owner: design later, build later]
Personal newsletters (email), automated tweets from the site, and
"most followed everything" (global follow counts). All three need a server
that receives follows/signups. When picked up: Netlify Functions + a small
store; scheduled digest generated from the same builders. Until then:
personal feed (שלי tab) + a build-time weekly digest page + share-to-X
links instead of a bot. **Security note (CWE-359, OWASP A01):** any future
signup must collect email only, double opt-in, no tracking pixels by
default; never publish MK personal contact data — official channels only.

### Parked / low priority
- Custom visual language via text-to-image: park until the data story is
  complete; current faction-colour/plenum visual system is the identity.
- Full Knesset-26 flip: gated on `probe_knesset.py` turning green.

## Git-connecting mandat121 (prepared, not yet linked)

Prepared 2026-11-10: root `netlify.toml` (publish `.`, `ignore` skips
builds when only `biapia/` changed; validated with `netlify build --dry`),
and `biapia/netlify.toml` gained the mirror-image `ignore` (skips game
builds on mandat121-only/data-only pushes) — so a push to `main` builds
each site only when its inputs changed, instead of always burning two
deploys. To link when the owner says go: Netlify → mandat121 → Site
configuration → Build & deploy → Link to Git repository →
`harelc/biapia`, branch `main`, base directory empty, (build settings
come from the root `netlify.toml`). Equivalent via API using the same
Netlify GitHub App installation as bia-pia. **Effect once linked:** every
push builds mandat121 — including the daily workflow's `data:` commits,
which is the intended auto-refresh — and the manual staging deploy below
is retired. Do not link/push without the owner's explicit go (owner rule).

## Deploy (current, manual)

> **OWNER RULE (2026-11-10): do NOT push to git remote and do NOT deploy
> to Netlify unless the owner explicitly says so in that session.** Deploys
> burn Netlify credits; `bia-pia` is also git-connected, so a push can
> trigger a deploy. Build and verify locally (`python3 -m http.server`)
> and leave deployment to the owner's call.

Sites claimed 2026-11-10: `mandat121` (the app) and `mandate121`
(placeholder whose index is a redirect to `mandat121` — Netlify does not
allow two `*.netlify.app` names on one site, so the second spelling is a
second, one-page site). The repo folder is linked to `mandat121`
(`.netlify/state.json`, gitignored); the game remains at bia-pia.

```bash
mkdir -p /tmp/mandat121_deploy
cp index.html /tmp/mandat121_deploy/index.html
cp -al data /tmp/mandat121_deploy/data
netlify deploy --prod --dir=/tmp/mandat121_deploy --site=52559b29-fcc2-46e8-8794-5dbe3effc3d3
```
(App file is root `index.html` since 2026-11-10; it was `open.html`.)

## Legacy decoupling — DONE 2026-11-10

המנדט ה־121 no longer reads the biapia game dataset (`data/bills.json`)
at runtime. Curated vote records are built in `load()` from the project's
own `k25_bills.json` (primary vote per bill) + per-MK rows joined from
`k25_vote_results.json`, with faction-on-the-day from the new
`faction_stints` in `k25_tenure.json` (PositionID=54 stints; 149 MKs, 40
with >1 stint). The MK index now spans **all 7,451 era votes** (e.g. the
busiest MK's page lists ~6,480 documented votes, not the old curated
subset), and each history row opens its own vote page. Record id == bill
id, so existing localStorage `myVotes` keys carry over. Verified: no
`bills.json` request in the network log; 1,152 native curated records.
Remaining build-time couplings (not runtime): `build_knesset.py` merges
topics/synopses/doc from the legacy file into `k25_bills.json`, and
`build_committees.py` still reads it for its bill set — replace both with
`k25_bills.json`-native sources when topics are regenerated (P2 keyword
topology). The biapia game now lives in `biapia/` (see Repo layout above) and is parked as-is.

Vote linkage fixed 2026-11-10: `build_knesset.py` originally took each
bill's `votes[]` from the legacy file (1,177 bills). It now links era
votes natively (`ItemID` → bill, reading-family kinds) with tallies
counted from `k25_vote_results.json`: **1,627 bills with votes, 1,597
curated primary-vote records** in the app. Coverage reality check: of
7,557 era votes, ~7,470 have per-MK results (all get an MK map on their
era-vote page); they cover only 1,632 distinct bills because 3,076 are
reservation votes and many bills are voted on repeatedly — most of the
7,491 bills (4,413) never reached a plenum vote at all. The votes tab
therefore defaults to "כל הצבעות המליאה" (with maps); the per-bill
primary-vote view is the secondary toggle.

## Working rules
- Static site, builders in Python (stdlib), JSON snapshots in `data/`.
- Every derived claim labels its coverage (harvested vs total).
- Official sources only: OData, `fs.knesset.gov.il`, `www.knesset.gov.il`
  WebSiteApi. `main.knesset.gov.il` is Radware-walled to scripts — never
  depend on scraping it.
