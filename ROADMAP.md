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
  (~3M words): word/prefix match ranked by mentions, deep-links to session.
  Since 2026-11-10 the index is a sharded inverted index
  (`data/search_c/`, `data/search_p/` — every content word → sittings,
  sharded by first two chars, fetched on demand per query); the previous
  top-120-words-per-sitting index silently missed rarer words.
- ~~Word clouds (global + per-protocol)~~ — REMOVED 2026-11-10, owner
  call: frequency clouds were noise, not insight. UI, builder output
  (`protocol_cloud.json`, `top_words`) and fetch all deleted; the
  protocol *search* above stays.

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

✅ DONE 2026-11-10: plenum protocols — first-class.
Promoted 2026-11-10 from a segment inside the committees tab to its own
top-level **מליאה** tab (view `plenum`; a sitting is `plenumSession`,
reader still parsed on demand — owner call).
`build_plenum_search.py` → `data/plenum_search.json`: word/prefix search
over **all 418 K25 plenum sessions' דברי הכנסת**
(`KNS_DocumentPlenumSession`, GroupTypeID=28). Browsable sitting index
+ a page per sitting (that day's votes, prev/next navigation) +
back-links from every vote page. Reader is parsed **on demand** by
`netlify/functions/plenum.mjs` — the search-only call was reversed by
the owner the same day; pre-parsing 418 × ~0.5MB docx (100MB+ in repo)
stays rejected, and fs.knesset sends no CORS headers so the browser
cannot fetch transcripts itself. Builder is incremental (cached
sessions skipped), wired into `knesset-daily.yml`.

### P1. OData gaps — what we are not using yet
✅ DONE 2026-11-10: **`build_odata_extras.py` → `data/k25_extras.json`**
(wired into `knesset-daily.yml`, ~5 min run), surfaced in the UI:
`KNS_Query`+`KNS_DocumentQuery` (שאילתות per MK, with answer-doc links),
`KNS_Agenda` (הצעות לסדר per MK) → MK page cards; `KNS_BillUnion`/
`KNS_BillSplit`/`KNS_BillName` (מיזוגים/פיצולים/שמות לפי קריאה, 1,763
bills) + `KNS_IsraelLaw` name-match + `MagazineNumber`/`PageNumber`
רשומות citations (573 bills) → bill-page "שושלת ההצעה ופרסומה" card;
`KNS_SecondaryLaw` (2,847 items, 44 committees) + `KNS_JointCommittee`
(21 mappings) → committee page; `KNS_BroadcastCommitteSession`
(7,054 archive URLs — recent sessions have none yet; older agenda-shard
sessions do) → session-page 📺 button. Notes: `KNS_IsraelLawBinding`
is sparse/empty per-law — the bill→law link is a name match, not the
binding table. Lobbyists are NOT in the ParliamentInfo container
(404 there despite `$metadata`); they live on the sibling service
`OdataV4/Lobbyist/` (`V_Lobbyists` 830 + `V_LobbyistsClients` 2,515)
→ "שדלנים" segment in the committees tab, labelled as a current
register snapshot. **Privacy: the feed carries ת"ז/ח"פ
(`IdentityNumber`/`CorpNumber`) — the builder whitelists fields and
never stores them (CWE-359).** `KNS_GovMinistry`/`KNS_Status` are used
inside the builder as id→name lookups.

### P2. Rich topic / keyword topology
Today's topics are a small closed typology (owner noted: starring a topic
is low-value while it's small). Replace/augment with keywords extracted
from bill texts + protocol word data (already indexed) → topic pages with
bills, MKs, votes, protocols per keyword; only then make topics followable.

### P2. Future schedule + follow-anything
✅ DONE 2026-11-10 (schedule half): **📅 בכנסת — סדר היום** calendar on the
home page — `build_agenda.py` → `data/agenda.json` (wired into
`knesset-daily.yml`): plenum order of the day
(`KNS_PlenumSession`+`KNS_PlmSessionItem`) and committee sittings
(`KNS_CommitteeSession`+`KNS_CmtSessionItem`) in a **day / week / month**
calendar (month grid clicks through to the day view; prev/next steps by the
active mode). Recent months (2 back / 1 ahead) ship inline in
`agenda.json`; **every month back to the Knesset's first session ships as a
`data/agenda/YYYY-MM.json` shard** (52 shards, 11,274 sessions at first
build) that the UI fetches and merges on first navigation to that month —
so the calendar scrolls back the whole term (Nov 2022 →) without one huge
file. Builder note: item rows are fetched in one `ge/le` range scan over
session IDs (chronological) filtered locally, not hundreds of `in (...)`
queries (~15× faster). Bill items
deep-link to bill pages, sittings open in-app (agenda sessions resolve in
`renderSessionDetail` too), followed committees are ★-highlighted, and
prev/next navigation steps day/week/month across the whole term.
Honest recess behaviour: OData creates session rows only when the Knesset
publishes them (verified 2026-11-10: no rows at all for 8–14 Nov, last
plenum 2026-07-28), so an unpublished week is labelled as such and the
latest published week is shown instead — never backfilled.
Still open: follow per MK/session (committee follows already exist).
Follows stay local (localStorage) for now; "most followed" is impossible
until a backend exists — see Deferred.

### P2. Knesset explainer + Wikipedia abstracts
Explainer pages (how a bill becomes law, what a committee does…) —
partly present in Bill Journey copy; formalise.
✅ DONE 2026-11-10 (abstracts + X discovery): `build_mk_wiki.py` →
`data/mk_wiki_abstracts.json` — Hebrew Wikipedia lead abstracts for
**157/157** MKs who served in K25 (CC BY-SA, attributed + linked in a 📖
card on the MK page). Resolution: 120 curated mapping + 5 name-variant
aliases + 32 Wikipedia-search hits accepted only when Wikidata verifies
P39=Knesset-member (Q4047513), or the abstract itself says חבר/ת הכנסת —
0 unresolved. Plus Wikidata P2002 X handles: 104 MKs, **28 current-roster
MKs have one on Wikidata that the Knesset lobby does not publish**
(e.g. @itamarbengvir, @gantzbe, @NirBarkat) — shown only as a visibly
separate "מויקינתונים" chip (a Wikidata-only card for former MKs), never
merged into `mk_contacts.json`. Live spot-check 2026-11-10: 50/52 unique
discovered handles returned 200 on x.com; the 2 dead ones (@Moshesolomonr,
@KahanaMatan, both 404) are filtered via a dated denylist in the builder.
2 handle mismatches vs the Knesset are reported (official wins in UI).
Still open: the explainer pages themselves.
Pre-public cleanup DONE 2026-11-10 (repo still private — flip is the
owner's, in GitHub settings, after a skim of ROADMAP/MANDAT121_DESIGN as
public reading): `.claude/settings.local.json` untracked + `.claude/`
gitignored; local git identity switched to
`harelc@users.noreply.github.com` (the 26 existing commits still carry
the old work-email author metadata — public once flipped, accepted
unless history is rewritten); `LICENSE` added (code MIT · content
CC BY-NC-SA 4.0 · Knesset/Wikipedia/Wikidata data under their own
terms), linked from README; secret scan of tree + full history clean
(env values live only in Netlify; only env-var *names* in code).
✅ DONE 2026-11-10 (about/impressum): `#tab=about` view linked from the
footer (not a 9th tab) — mission, operator (הראל קין, independent,
non-commercial, not an official Knesset site), GitHub-Issues contact /
corrections channel (no postal address or personal email published, by
design), sources & methodology, privacy (local-only follows, first-party
daily-salted IP-hash counter), accuracy disclaimer, CC BY-NC-SA 4.0 +
credits, plus a short English Impressum paragraph.

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

## Git-connecting mandat121 — LINKED 2026-11-10

Done on owner's "go" (commit `cd380cf`, pushed to `main`): mandat121 is
git-linked to `harelc/mandat121@main` (repo renamed from `harelc/biapia` 2026-11-10; GitHub redirects the old slug, both Netlify sites repointed) (base = root, via Netlify API), and
bia-pia builds from `biapia/`. Both sites deployed `ready` from that push
and were live-verified (mandat121 = mandate app with full analytics;
bia-pia = the game, data assembled by `build_site.sh`). From now on:
push to `main` = deploy (each site built only when its `ignore` guard
passes), and the daily workflow's data commits will auto-republish
mandat121. The manual staging deploy below is retired (kept for reference).

Prepared 2026-11-10: root `netlify.toml` (publish `.`, `ignore` skips
builds when only `biapia/` changed; validated with `netlify build --dry`),
and `biapia/netlify.toml` gained the mirror-image `ignore` (skips game
builds on mandat121-only/data-only pushes) — so a push to `main` builds
each site only when its inputs changed, instead of always burning two
deploys. To link when the owner says go: Netlify → mandat121 → Site
configuration → Build & deploy → Link to Git repository →
`harelc/mandat121`, branch `main`, base directory empty, (build settings
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
at runtime. Per-MK rows are joined from `k25_vote_results.json`, with
faction-on-the-day from the new `faction_stints` in `k25_tenure.json`
(PositionID=54 stints; 149 MKs, 40 with >1 stint). The MK index spans
**all era votes with per-MK results** (e.g. the busiest MK's page lists
~6,480 documented votes), and each history row opens its own vote page.
Verified: no `bills.json` request in the network log.

**No "curated/primary vote" concept (removed 2026-11-10):** there is one
kind of vote — a plenum vote, keyed everywhere (routes `#vote=<id>`,
localStorage `myVotes`, MK histories) by its vote id, normalised by
`voteModel()`. A vote may link to a bill (`item_id`); the bill layers on
context (journey, topics, text, ☆ follow) but never a second identity.
The old split keys (bill id for a bill's chosen vote, `__v<voteId>`
otherwise) are migrated to vote ids in `migrateMyVotes()` at load —
unambiguous because bill ids (1,038,990+) and vote ids (37,658–46,701)
never overlap. Bill ☆-following stays: the star lives on the bill page,
and on a vote page it stars that vote's bill.
Remaining build-time couplings (not runtime): `build_knesset.py` merges
topics/synopses/doc from the legacy file into `k25_bills.json`, and
`build_committees.py` still reads it for its bill set — replace both with
`k25_bills.json`-native sources when topics are regenerated (P2 keyword
topology). The biapia game now lives in `biapia/` (see Repo layout above) and is parked as-is.

Vote linkage fixed 2026-11-10: `build_knesset.py` originally took each
bill's `votes[]` from the legacy file (1,177 bills). It now links era
votes natively (`ItemID` → bill, reading-family kinds) with tallies
counted from `k25_vote_results.json`: **1,627 bills with votes**. Coverage reality check: of
7,557 era votes, ~7,470 have per-MK results (all get an MK map on their
vote page); they cover only 1,632 distinct bills because 3,076 are
reservation votes and many bills are voted on repeatedly — most of the
7,491 bills (4,413) never reached a plenum vote at all. The votes tab is
the single list of all plenum votes, sortable by newest / closest /
most voters.

## Working rules
- Static site, builders in Python (stdlib), JSON snapshots in `data/`.
- Every derived claim labels its coverage (harvested vs total).
- Official sources only: OData, `fs.knesset.gov.il`, `www.knesset.gov.il`
  WebSiteApi. `main.knesset.gov.il` is Radware-walled to scripts — never
  depend on scraping it.
