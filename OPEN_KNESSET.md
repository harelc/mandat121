# המנדט ה־121 (working name) — citizen Knesset tracker — design & prep

Name note: "כנסת פתוחה / Open Knesset" is the Hasadna brand — do not use. Working name **המנדט ה־121**: 120 mandates are handed out on election day; the 121st stays with the citizens and works all term (follow, "I would have voted", who represents me). Check trademark/domain before launch.

Status: **dry-run built on K25** — root `index.html` (renamed from `open.html` 2026-11-10; serve: `python3 -m http.server 8091`). Waiting on Knesset 26 OData (probe: `python3 probe_knesset.py`, exit 2 as of 2026-11-09); same UI flips to K26 when it publishes.
Decisions (2026-11-09): launch corpus = K25 now → K26 when live; scope = all views. Product center is **citizen tracking** (follow bills/MKs/topics, "since your last visit", faction rebels, loyalty/cohesion) — the biapia "I would have voted X" is one lens inside it (`שלי` tab + per-bill toggle), not the app itself.
K26 roster preview: `build_k26_preview.py` snapshots `../elections-vote-transfer` CEC candidate lists + official seats → `data/k26_preview.json` (120 elected, in list order). Incoming MKs who also sat in K25 link straight to their K25 voting record.
Site architecture (index.html): **one app, per-Knesset instances** — header switcher כנסת 25 (full coverage) / כנסת 26 (building up). Each instance has its own MK roster, party registry + colours, hemicycles (command-centre ARC_ORDER, row-first per party, initials in seats), and its own local state (my votes/follows are stored per Knesset). K26 fills automatically as OData entities for KnessetNum=26 appear; until then its bills/committees views are explicit "not yet published" states, and its MK cards link back to the member's K25 record.
Full K25 corpus (built 2026-11-09): `build_knesset.py --knesset 25` reads OData directly → `data/k25_bills.json` (7,491 bills, 6,877 with initiators) + `data/k25_votes.json` (all 7,557 era plenum votes, classified: 3,076 reservations, 1,216 reading-2, 1,199 pre-1, 939 transfers, 556 reading-3, 224 no-confidence, 62 agendas). UI: חוקים tab toggles 🗳️ הצבעות / 📚 כל ההצעות with status/committee/type filters; non-vote bills open a lite detail (initiators, status, committee history, all era plenum events for the bill).
MK list order (built 2026-11-09): `build_k25_list_order.py` → `data/k25_list_order.json` — each current MK's position in their faction's original 2022 election-list sequence (source: he.wikipedia "הכנסת העשרים וחמש" MK table; gov.il CEC pages 403 scripted fetches). 120/120 matched. Used for: within-party seat order in both K25 hemicycles (party leader at the front of the wedge), seat tooltips, faction-filtered MK grid, and the MK card ("מקום N בסדר רשימת הבחירות 2022"). Note for joint 2022 lists (e.g. הציונות הדתית+עוצמה יהודית+נעם) the number is the position within the faction's own sequence, not the joint-list number.
Vote-plenum seating (fixed 2026-11-09): every MK who held a seat on the vote date gets their fixed seat (party wedge by `ARC_ORDER_K25`, inside the wedge by 2022 list rank) — absentees no longer sort to the back. Membership on a date comes from `build_k25_tenure.py` → `data/k25_tenure.json` (official `KNS_PersonToPosition`, KnessetNum=25, PositionID 43/61; 148/148 served MKs matched, Norwegian-law stints handled). MKs who left mid-term are seated in their party wedge for votes held while they served (tooltip: "כיהן/ה אז, עזב/ה מאז"), never in today's standing plenum. Verified: early (2023) and late (2026) votes both render exactly 120 seats.
Committee session pages are in-app (`openSession`): the official `AllCommitteesAgenda.aspx?ItemID=` deep link drops its parameter and lands on a generic agenda page (and `main.knesset.gov.il` bot-walls scripted agents, HTTP 247), so sessions render from our OData snapshot — agenda items, location, bills discussed — and link out only to stable `fs.knesset.gov.il` document files (incl. פרוטוקול ועדה), which deep-link fine.
Committee layer (K25, built): `build_committees.py` reads OData directly (KNS_Committee/Session, KNS_CmtSessionItem, KNS_DocumentCommitteeSession, KNS_Status) → `data/committees.json`: 90 committees, committee history for 801 dataset bills (4,003 bill-session items with status-at-the-time), and 362 recent sessions (since the plenum cutoff) with agenda items + 1,095 documents. In the UI: per-bill "עבודת הוועדה" timeline, committee feed on home, committee cards on the Knesset tab. Note answers "aren't we reading OData ourselves": yes — all data layers are built by reading the official OData API directly (never the Radware-blocked site); the browser only loads the built JSON snapshots.

## Why this beats knesset.gov.il

The official site is a set of disconnected databases (bills / votes / committees / queries), each with its own search, no journey view, no plain-language summaries, and vote pages that bury the per-MK record. The same data, joined once, gives what the site never shows:

1. **One bill = one story.** Every bill as a single timeline from submission to ספר החוקים (or death), with the people, committee, documents and every vote on that path.
2. **Plain Hebrew first.** AI synopsis (existing biapia pipeline) + official `SummaryLaw` when enacted; legal text is one click deeper, not the entry point.
3. **Every vote, not just final ones.** Reservation (הסתייגות) votes — the majority of plenum votes — plus no-confidence, transfer-to-committee and agenda motions, classified and linked to the bill/stage they belong to.
4. **People over time.** Faction/role on the day of each vote (`KNS_PersonToPosition` date ranges), not today's faction pasted onto history.

## The five views

| View | Question it answers | Core entities |
|---|---|---|
| **היום בכנסת (Pulse)** | What's happening today/this week? | `KNS_PlenumSession`, `KNS_CommitteeSession` (+broadcast), today's `KNS_PlenumVote` |
| **מסע חוק (Bill Journey)** — hero view | Where is this law, how did it get here, who moved/killed it? | `KNS_Bill`+`KNS_Status`, `KNS_PlmSessionItem`, `KNS_CmtSessionItem`, `KNS_DocumentBill`, all `KNS_PlenumVote(Result)` for the bill, `KNS_BillInitiator`, `KNS_BillUnion/Split/Name`, `KNS_IsraelLawBinding` |
| **הצבעות (Vote Explorer)** | What was actually decided, how close, who broke ranks? | `KNS_PlenumVote(Result)` with vote-type taxonomy; plenum SVG reused from biapia |
| **ח״כ (MK Profile)** | What does this MK do all day? | `KNS_Person(ToPosition)`, votes, `KNS_BillInitiator`, `KNS_Query`, `KNS_Agenda` |
| **ועדה (Committee Hub)** | Where bills actually get rewritten | `KNS_Committee(Session)`, `KNS_CmtSessionItem`, `KNS_DocumentCommitteeSession`, `KNS_BroadcastCommitteSession` |

Bill Journey timeline stages (from status/session evidence, ordered by date):
`הונחה / דיון מוקדם → ועדת הכנסת (קביעת ועדה) → הכנה לקריאה ראשונה (ועדה) → קריאה ראשונה → הכנה ל-2/3 (ועדה) → קריאה שנייה (עם הסתייגויות מתקפלות) → קריאה שלישית → ספר החוקים (PublicationDate + MagazineNumber/PageNumber + IsraelLaw link)`, with terminal branches: `נעצרה / מוזגה / פוצלה / הוסבה להצעה לסדר`.

Design: RTL Hebrew, Heebo/Rubik (as biapia), timeline-first layout, faction colors consistent with biapia's plenum, every claim deep-links to the official bill/vote page or the `fs.knesset.gov.il` document. No gamification, no scores.

## Data model (build output, static JSON as in biapia)

```
data/knesset{N}/
  persons.json     PersonID -> {name, photo?, roles:[{faction, position, from, to}], wiki}
  factions.json    KNS_Faction per Knesset
  committees.json  KNS_Committee
  statuses.json    KNS_Status (TypeID=2) — cached once, all Knessets
  bills.json       BillID -> {name, names_by_reading, type, initiators:[PersonID],
                             committee, status_now, stages:[{stage, date, session_ref,
                             status_id}], documents:{reading1, reading23, law_reshumot,...},
                             israel_law_id?, publication:{date, magazine, page},
                             synopsis, topics}
  votes.json       VoteID -> {bill_id, datetime, session_id, kind, reading,
                             for_desc, counts, results:[{person_id, result}]}
  sessions.json    plenum + committee sessions with their items
```
`kind` taxonomy over `ForOptionDesc` (classify, don't substring-match two patterns):
3rd / 2nd / 1st / pre-1st(דיון מוקדם) / reservation(הסתייגות) / transfer-to-committee / no-confidence / agenda / secondary-legislation / other.

## Pipeline (new, replaces name-matching)

1. `probe_knesset.py` gate — proceed only when K26 counts > 0.
2. Fetch **by PersonID joins only**: `KNS_PersonToPosition` (PositionID=54) → persons/factions; votes → results (`MkId`); bills → initiators/documents/status via `$expand` where it helps (verified working).
3. Incremental by watermark per Knesset: `KNS_Bill.LastUpdatedDate`, `KNS_PlenumVote VoteDateTime`, session `LastUpdatedDate` — with 7-day overlap as in current scripts.
4. Reconstruct stages from `KNS_PlmSessionItem`/`KNS_CmtSessionItem` status snapshots + session dates (status codes alone have no order: `OrderTransition` is null, descriptions duplicate).
5. Enrich: existing `generate_synopses.py` / `generate_topics.py` (Claude), photos/Wikipedia as in biapia, keyed by PersonID→name once at build time.
6. Emit static JSON; frontend is a single-page app like biapia (no server). Nightly rebuild via scheduled job once K26 is live.

**Hard rules carried over from the K25 mess** (see `KNESSET_APIS.md` Nov 2026 section):
never infer Knesset from date (current `KNESSET_DATES` fallback silently labels K26 as 25); never key bills on one "best" vote (that deletes the journey); explicit `$skip/$top` pagination, don't trust `nextLink` on filtered queries; ignore `KNS_CommitteeSession.SessionUrl` (manual: use `KNS_BroadcastCommitteSession`); `main.knesset.gov.il` is Radware-blocked to scripts (474) — OData + `fs.knesset.gov.il` only.

## K25 snapshot (recent months, live-probed 2026-11-09)

- Local `data/bills.json`: 1,398 bills (K25: 1,188), votes through **2026-06-24**, 1,210 with synopses. **Stale by ~5 weeks of plenum + 4 months of committee/bill updates.**
- Live since that cutoff: **481 plenum votes** (2026-06-24→07-28, 106 unique bills; only 38 were 3rd-reading, 258 were reservations), last plenum 2026-07-28; **806 K25 bills updated**, committee sessions to 2026-11-04. Updating biapia today ≈ +70–100 quiz bills after 3rd-reading dedup.
- K25 totals in feed: 7,491 bills, 418 plenum sessions, 10,856 committee sessions, 1,580 queries, 798 agendas, 16 factions (several defunct — splits are real), 90 committee incarnations.

## Decisions to make before build

1. **Scope for v1**: Bill Journey + Vote Explorer + Pulse only (MK/Committee as stub links), or all five views?
2. **Corpus**: launch on late-K25 data now (real, testable today) and flip to K26 when the probe turns green, or wait for K26 day-one?
3. **Biapia in parallel**: refresh biapia to the July K25 votes now (cheap: existing incremental scripts + synopses), and/or re-point it at K26 once seated?
4. **Enrichment cost**: synopsis per *bill* (current, ~1.2k) vs per *stage document* for the journey view — the latter is the captivating part and the expensive part.
