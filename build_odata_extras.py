#!/usr/bin/env python3
"""P1 OData gaps harvest -> data/k25_extras.json.

Entities the original plan listed but nothing read yet (ROADMAP P1):
  KNS_Query + KNS_DocumentQuery   שאילתות (per MK, ministry, answered?)
  KNS_Agenda + KNS_DocumentAgenda הצעות לסדר (per MK, with their documents)
  KNS_DocumentSecondaryLaw        documents of the secondary legislation
  KNS_IsraelLaw (+ bill fields)   enacted-law link + רשומות citation
  KNS_BillUnion / KNS_BillSplit / KNS_BillName
                                  merged/split lineage + names per reading
  KNS_SecondaryLaw                חקיקת משנה per committee
  KNS_BroadcastCommitteSession    session video/archive links
  KNS_JointCommittee              joint-committee composition
  KNS_GovMinistry, KNS_Status     lookups for the above
  V_Lobbyists(+Clients)           lobbyist register — served from the
                                  sibling /OdataV4/Lobbyist/ root (the
                                  ParliamentInfo root 404s these sets).

Privacy (CWE-359): the lobbyist feed carries IdentityNumber/CorpNumber.
They are NEVER copied — the builder whitelists public register fields
(name, firm, permit type) only.

Output sections are keyed for direct UI lookup: queries_by_mk,
agendas_by_mk (surname-first names), bill_links, laws (by bill id),
secondary_by_committee, broadcasts (by committee-session id), joint
(by committee id), lobbyists (current register snapshot, not K-scoped).

Change probes: every section is a full paged harvest and the whole
builder used to take ~10 minutes (~600 requests) even on days the
Knesset published nothing. Each section now first asks the feed one
cheap question — the newest LastUpdatedDate or the row count of its
source entity, plus a fingerprint of the local inputs it depends on —
and is rebuilt only when that probe moved; otherwise the previous
output section is carried over verbatim. Probes are kept in
data/k25_extras_state.json (builder state, like build_knesset's
watermark). A document added to a query/agenda/secondary-law without
its parent row changing would wait for the parent to move; in the
feed, documents are published together with the parent row.
"""
import hashlib
import json
import re
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

PI = "https://knesset.gov.il/OdataV4/ParliamentInfo/"
LB = "https://knesset.gov.il/OdataV4/Lobbyist/"
HDR = {"User-Agent": "Mozilla/5.0 mandat121-build", "Accept": "application/json"}
KN = 25


def get(base, path):
    for attempt in range(3):
        try:
            req = urllib.request.Request(base + path, headers=HDR)
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def paged(base, entity, params):
    """OData caps pages at 100 rows. Handles both wrapped {value:[...]}
    responses and the bare-array style of KNS_DocumentQuery."""
    rows, skip = [], 0
    while True:
        j = get(base, f"{entity}?{params}&$top=100&$skip={skip}")
        page = j["value"] if isinstance(j, dict) else j
        rows.extend(page)
        if len(page) < 100:
            return rows
        skip += 100


def in_batches(base, entity, field, ids, select, extra=""):
    out = []
    ids = list(ids)
    for i in range(0, len(ids), 25):
        batch = ",".join(str(x) for x in ids[i:i + 25])
        out += paged(base, entity,
                     f"$filter={field}%20in%20({batch}){extra}&$select={select}")
    return out


def person_map(ids):
    out = {}
    for r in in_batches(PI, "KNS_Person", "Id", ids, "Id,FirstName,LastName"):
        name = f"{r.get('LastName','')} {r.get('FirstName','')}".strip()
        out[r["Id"]] = name.replace("׳", "'").replace("״", '"')
    return out


# --- change probes ---------------------------------------------------------
def max_updated(base, entity, filt=""):
    """Newest LastUpdatedDate in an entity (1 request), '' if empty."""
    f = f"$filter={filt}&" if filt else ""
    j = get(base, f"{entity}?{f}$orderby=LastUpdatedDate%20desc&$top=1&$select=LastUpdatedDate")
    rows = j["value"] if isinstance(j, dict) else j
    return (rows[0].get("LastUpdatedDate") or "") if rows else ""


def row_count(base, entity, filt=""):
    f = f"$filter={filt}&" if filt else ""
    j = get(base, f"{entity}?{f}$count=true&$top=0")
    return j.get("@odata.count", -1) if isinstance(j, dict) else -1


def fingerprint(obj):
    return hashlib.sha1(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


# --- sections (each returns its output section) ----------------------------
def build_queries(queries, persons, statuses, ministries):
    qdocs = defaultdict(list)
    for d in in_batches(PI, "KNS_DocumentQuery", "queryID",
                        [q["Id"] for q in queries], "queryID,groupTypeDesc,filePath"):
        # with $select this entity answers in PascalCase, without in camelCase
        qid = d.get("queryID", d.get("QueryID"))
        qdocs[qid].append({"group": d.get("groupTypeDesc") or d.get("GroupTypeDesc") or "",
                           "url": (d.get("filePath") or d.get("FilePath") or "").replace("\\", "/")})
    queries_by_mk = {}
    for q in sorted(queries, key=lambda x: x.get("SubmitDate") or "", reverse=True):
        name = persons.get(q.get("PersonID"))
        if not name:
            continue
        e = queries_by_mk.setdefault(name, {"total": 0, "answered": 0, "items": []})
        e["total"] += 1
        answered = q.get("StatusID") == 9  # נענתה
        e["answered"] += answered
        if len(e["items"]) < 15:
            e["items"].append({
                "name": q["Name"], "type": q.get("TypeDesc") or "",
                "ministry": ministries.get(q.get("GovMinistryID"), ""),
                "answered": bool(answered),
                "status": statuses.get(q.get("StatusID"), ""),
                "submit": (q.get("SubmitDate") or "")[:10],
                "reply": (q.get("ReplyMinisterDate") or "")[:10],
                "docs": qdocs.get(q["Id"], [])})
    print(f"queries: {len(queries)} ({len(queries_by_mk)} MKs)", flush=True)
    return queries_by_mk


def build_agendas(agendas, persons, statuses, committees):
    adocs = defaultdict(list)
    for d in in_batches(PI, "KNS_DocumentAgenda", "AgendaID",
                        [a["Id"] for a in agendas], "AgendaID,GroupTypeDesc,FilePath"):
        adocs[d["AgendaID"]].append({"group": d.get("GroupTypeDesc") or "",
                                     "url": (d.get("FilePath") or "").replace("\\", "/")})
    agendas_by_mk = {}
    cmt_name = {c["id"]: c["name"] for c in committees["committees"]}
    for a in sorted(agendas, key=lambda x: x.get("LastUpdatedDate") or "", reverse=True):
        name = persons.get(a.get("InitiatorPersonID"))
        if not name:
            continue
        e = agendas_by_mk.setdefault(name, {"total": 0, "items": []})
        e["total"] += 1
        if len(e["items"]) < 12:
            e["items"].append({
                "name": a["Name"], "cls": a.get("ClassificationDesc") or "",
                "type": a.get("SubTypeDesc") or "",
                "status": statuses.get(a.get("StatusID"), ""),
                "committee": cmt_name.get(a.get("CommitteeID"), ""),
                "gov": a.get("GovRecommendationDesc") or "",
                "date": (a.get("LastUpdatedDate") or "")[:10],
                "docs": adocs.get(a["Id"], [])})
    print(f"agendas: {len(agendas)} ({len(agendas_by_mk)} MKs)", flush=True)
    return agendas_by_mk


def build_links(bill_ids):
    links = {}

    def link(bid):
        return links.setdefault(str(bid), {})

    for u in paged(PI, "KNS_BillUnion", "$select=MainBillID,UnionBillID"):
        if u["MainBillID"] in bill_ids or u["UnionBillID"] in bill_ids:
            if u["MainBillID"] in bill_ids:
                link(u["MainBillID"]).setdefault("merged_from", []).append(u["UnionBillID"])
            if u["UnionBillID"] in bill_ids:
                link(u["UnionBillID"])["merged_into"] = u["MainBillID"]
    for s in paged(PI, "KNS_BillSplit", "$select=MainBillID,SplitBillID,Name"):
        if s["MainBillID"] in bill_ids or s["SplitBillID"] in bill_ids:
            if s["MainBillID"] in bill_ids:
                link(s["MainBillID"]).setdefault("split_to", []).append(
                    {"id": s["SplitBillID"], "name": s.get("Name") or ""})
            if s["SplitBillID"] in bill_ids:
                link(s["SplitBillID"])["split_from"] = s["MainBillID"]
    for n in paged(PI, "KNS_BillName", "$select=BillID,Name,NameHistoryTypeDesc"):
        if n["BillID"] in bill_ids:
            link(n["BillID"]).setdefault("names", []).append(
                {"name": n["Name"], "stage": n.get("NameHistoryTypeDesc") or ""})
    print(f"bill links: {len(links)} bills", flush=True)
    return links


def build_laws(bills, bill_ids):
    def clean(s):
        s = re.sub(r",?\s*התש[א-ת\"״]{2,4}[-–]\d{4}\s*$", "", s or "")
        return re.sub(r"\s+", " ", s).strip()

    bill_by_clean = {clean(b["name"]): b["id"] for b in bills}
    laws = {}
    for law in paged(PI, "KNS_IsraelLaw",
                     f"$filter=KnessetNum%20eq%20{KN}"
                     "&$select=Id,Name,IsBasicLaw,PublicationDate,LatestPublicationDate,LawValidityDesc"):
        bid = bill_by_clean.get(clean(law["Name"]))
        if bid:
            laws[str(bid)] = {"law_id": law["Id"], "name": law["Name"],
                              "basic": bool(law.get("IsBasicLaw")),
                              "published": (law.get("PublicationDate") or "")[:10],
                              "validity": law.get("LawValidityDesc") or ""}
    pub_ids = [b["id"] for b in bills if b.get("publication_date")]
    for r in in_batches(PI, "KNS_Bill", "Id", pub_ids,
                        "Id,MagazineNumber,PageNumber"):
        e = laws.setdefault(str(r["Id"]), {})
        if r.get("MagazineNumber"):
            e["magazine"] = r["MagazineNumber"]
        if r.get("PageNumber"):
            e["page"] = r["PageNumber"]
    print(f"laws: {len(laws)} bills with law/citation", flush=True)
    return laws


def build_secondary(committee_ids):
    secondary = paged(PI, "KNS_SecondaryLaw",
                      f"$filter=KnessetNum%20eq%20{KN}"
                      "&$select=Id,Name,StatusName,CompletionCauseDesc,CommitteeID,PublicationDate,CommitteeApprovalDate")
    # Only the top-10 items per committee survive into the output, so only
    # those laws need their documents fetched. Fetching docs for every law
    # (≈2,850 ids in 25-id batches) made a rebuild cost ~2 min for the sake
    # of ≤440 visible items; collect the kept items first, then fetch just
    # their documents and attach them.
    secondary_by_committee = {}
    kept = []  # (law id, item dict) in output order
    for s in sorted(secondary, key=lambda x: x.get("PublicationDate") or "", reverse=True):
        cid = s.get("CommitteeID")
        if cid not in committee_ids:
            continue
        e = secondary_by_committee.setdefault(str(cid), {"total": 0, "items": []})
        e["total"] += 1
        if len(e["items"]) < 10:
            item = {"name": s["Name"], "status": s.get("StatusName") or "",
                    "cause": s.get("CompletionCauseDesc") or "",
                    "date": (s.get("PublicationDate") or s.get("CommitteeApprovalDate") or "")[:10],
                    "docs": []}
            e["items"].append(item)
            kept.append((s["Id"], item))
    sdocs = defaultdict(list)
    for d in in_batches(PI, "KNS_DocumentSecondaryLaw", "SecondaryLawId",
                        [law_id for law_id, _ in kept], "SecondaryLawId,GroupTypeDesc,FilePath"):
        sdocs[d["SecondaryLawId"]].append({"group": d.get("GroupTypeDesc") or "",
                                           "url": (d.get("FilePath") or "").replace("\\", "/")})
    for law_id, item in kept:
        item["docs"] = sdocs.get(law_id, [])
    print(f"secondary: {len(secondary)} (in {len(secondary_by_committee)} committees)", flush=True)
    return secondary_by_committee


def build_broadcasts(all_session_ids):
    broadcasts = {}
    for r in in_batches(PI, "KNS_BroadcastCommitteSession", "Id", all_session_ids,
                        "Id,BroadcastUrl"):
        if r.get("BroadcastUrl"):
            broadcasts[str(r["Id"])] = r["BroadcastUrl"]
    return broadcasts


def build_joint(committee_ids):
    joint = {}
    for r in paged(PI, "KNS_JointCommittee", "$select=CommitteeID,ParticipantCommitteeID"):
        if r["CommitteeID"] in committee_ids and r["ParticipantCommitteeID"] in committee_ids:
            joint.setdefault(str(r["CommitteeID"]), []).append(r["ParticipantCommitteeID"])
    return joint


def build_lobbyists():
    # Whitelist only — IdentityNumber/CorpNumber in the feed are dropped.
    clients = defaultdict(list)
    for c in paged(LB, "V_LobbyistsClients", "$select=LobbyistID,Name"):
        clients[c["LobbyistID"]].append(c["Name"])
    lobbyists = []
    for l in paged(LB, "V_Lobbyists",
                   "$select=Id,FullName,PermitTypeValue,CorporationName,IsIndependent,PracticeFramework"):
        lobbyists.append({"name": l.get("FullName") or "",
                          "firm": l.get("CorporationName") or "",
                          "permit": l.get("PermitTypeValue") or "",
                          "independent": bool(l.get("IsIndependent")),
                          "framework": l.get("PracticeFramework") or "",
                          "clients": sorted(set(clients.get(l["Id"], [])))})
    lobbyists.sort(key=lambda x: (-len(x["clients"]), x["name"]))
    print(f"lobbyists: {len(lobbyists)}", flush=True)
    return lobbyists


def all_session_ids_for_broadcasts(session_ids):
    """Broadcast archive rows exist mainly for older sessions; the recent
    committee sessions have none yet. Intersect with every session id the
    UI can open: committee recents + all agenda shards."""
    ids = set(session_ids)
    for shard in Path("data/agenda").glob("*.json"):
        try:
            data = json.loads(shard.read_text(encoding="utf-8"))
        except Exception:
            continue
        for key in ("plenum", "committees"):  # shard shape: {month, plenum:[], committees:[]}
            for s in data.get(key, []) if isinstance(data, dict) else []:
                if isinstance(s, dict) and s.get("id"):
                    ids.add(s["id"])
    return ids


def main():
    bills = json.load(open("data/k25_bills.json", encoding="utf-8"))
    bill_ids = {b["id"] for b in bills}
    committees = json.load(open("data/committees.json", encoding="utf-8"))
    committee_ids = {c["id"] for c in committees["committees"]}
    session_ids = {s["id"] for s in committees.get("recent_sessions", [])}

    prev_path = Path("data/k25_extras.json")
    prev = {}
    if prev_path.exists():
        try:
            prev = json.loads(prev_path.read_text("utf-8"))
        except Exception:
            prev = {}
    state_path = Path("data/k25_extras_state.json")
    old_probes = {}
    if state_path.exists():
        try:
            old_probes = json.loads(state_path.read_text("utf-8")).get("probes", {})
        except Exception:
            old_probes = {}
    probes = {}

    def need(section, probe):
        probes[section] = probe
        stale = old_probes.get(section) != probe or section not in prev
        print(f"  [{section}] probe {'CHANGED -> rebuild' if stale else 'unchanged -> reuse'}",
              flush=True)
        return stale

    statuses = {r["Id"]: r["Desc"] for r in paged(PI, "KNS_Status", "$select=Id,Desc")}
    ministries = {}
    for r in paged(PI, "KNS_GovMinistry", "$select=Id,Name,IsActive"):
        ministries.setdefault(r["Id"], r["Name"])

    # ---- שאילתות + הצעות לסדר (per MK) -------------------------------
    build_q = need("queries_by_mk", max_updated(PI, "KNS_Query", f"KnessetNum%20eq%20{KN}"))
    build_a = need("agendas_by_mk", max_updated(PI, "KNS_Agenda", f"KnessetNum%20eq%20{KN}"))
    queries = paged(PI, "KNS_Query",
                    f"$filter=KnessetNum%20eq%20{KN}"
                    "&$select=Id,Name,TypeDesc,StatusID,PersonID,GovMinistryID,SubmitDate,ReplyMinisterDate") if build_q else []
    agendas = paged(PI, "KNS_Agenda",
                    f"$filter=KnessetNum%20eq%20{KN}"
                    "&$select=Id,Name,ClassificationDesc,SubTypeDesc,StatusID,InitiatorPersonID,CommitteeID,GovRecommendationDesc,LastUpdatedDate") if build_a else []
    persons = person_map({q["PersonID"] for q in queries if q.get("PersonID")}
                         | {a["InitiatorPersonID"] for a in agendas if a.get("InitiatorPersonID")}) \
        if (build_q or build_a) else {}
    queries_by_mk = build_queries(queries, persons, statuses, ministries) \
        if build_q else prev.get("queries_by_mk", {})
    agendas_by_mk = build_agendas(agendas, persons, statuses, committees) \
        if build_a else prev.get("agendas_by_mk", {})

    # ---- bill lineage: unions, splits, names per reading ---------------
    links_probe = "|".join([
        str(row_count(PI, "KNS_BillUnion")), str(row_count(PI, "KNS_BillSplit")),
        str(row_count(PI, "KNS_BillName")), fingerprint(sorted(bill_ids))])
    links = build_links(bill_ids) if need("bill_links", links_probe) \
        else prev.get("bill_links", {})

    # ---- enacted laws: name-matched + רשומות citation ------------------
    laws_probe = "|".join([
        max_updated(PI, "KNS_IsraelLaw", f"KnessetNum%20eq%20{KN}"),
        fingerprint([[b["id"], b.get("publication_date"), b.get("magazine"),
                      b.get("page"), b.get("name")] for b in bills])])
    laws = build_laws(bills, bill_ids) if need("laws", laws_probe) \
        else prev.get("laws", {})

    # ---- secondary legislation per committee ---------------------------
    secondary_probe = "|".join([
        max_updated(PI, "KNS_SecondaryLaw", f"KnessetNum%20eq%20{KN}"),
        fingerprint(sorted(committee_ids))])
    secondary_by_committee = build_secondary(committee_ids) \
        if need("secondary_by_committee", secondary_probe) \
        else prev.get("secondary_by_committee", {})

    # ---- broadcasts + joint composition --------------------------------
    all_session_ids = all_session_ids_for_broadcasts(session_ids)
    broadcasts_probe = "|".join([
        str(row_count(PI, "KNS_BroadcastCommitteSession")),
        fingerprint(sorted(all_session_ids))])
    broadcasts = build_broadcasts(all_session_ids) \
        if need("broadcasts", broadcasts_probe) else prev.get("broadcasts", {})
    joint = build_joint(committee_ids) \
        if need("joint", str(row_count(PI, "KNS_JointCommittee"))) \
        else prev.get("joint", {})
    print(f"broadcasts: {len(broadcasts)}, joint maps: {len(joint)}", flush=True)

    # ---- lobbyist register (current; NOT Knesset-scoped) ---------------
    lobbyists_probe = "|".join([
        str(row_count(LB, "V_Lobbyists")), str(row_count(LB, "V_LobbyistsClients"))])
    lobbyists = build_lobbyists() if need("lobbyists", lobbyists_probe) \
        else prev.get("lobbyists", [])

    out = {
        "_meta": {"knesset": KN,
                  "note": "Lobbyists = current register snapshot (not Knesset-scoped); ID numbers from the feed are never stored."},
        "queries_by_mk": queries_by_mk,
        "agendas_by_mk": agendas_by_mk,
        "bill_links": links,
        "laws": laws,
        "secondary_by_committee": secondary_by_committee,
        "broadcasts": broadcasts,
        "joint": joint,
        "lobbyists": lobbyists,
    }
    Path("data/k25_extras.json").write_text(
        json.dumps(out, ensure_ascii=False), encoding="utf-8")
    state_path.write_text(json.dumps({"probes": probes}, ensure_ascii=False), "utf-8")
    print(f"DONE -> data/k25_extras.json")


if __name__ == "__main__":
    main()
