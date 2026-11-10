#!/usr/bin/env python3
"""P1 OData gaps harvest -> data/k25_extras.json.

Entities the original plan listed but nothing read yet (ROADMAP P1):
  KNS_Query + KNS_DocumentQuery   שאילתות (per MK, ministry, answered?)
  KNS_Agenda                      הצעות לסדר (per MK)
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
"""
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


def main():
    bills = json.load(open("data/k25_bills.json", encoding="utf-8"))
    bill_ids = {b["id"] for b in bills}
    committees = json.load(open("data/committees.json", encoding="utf-8"))
    committee_ids = {c["id"] for c in committees["committees"]}
    session_ids = {s["id"] for s in committees.get("recent_sessions", [])}

    statuses = {r["Id"]: r["Desc"] for r in paged(PI, "KNS_Status", "$select=Id,Desc")}
    ministries = {}
    for r in paged(PI, "KNS_GovMinistry", "$select=Id,Name,IsActive"):
        ministries.setdefault(r["Id"], r["Name"])

    # ---- שאילתות + הצעות לסדר (per MK) -------------------------------
    queries = paged(PI, "KNS_Query",
                    f"$filter=KnessetNum%20eq%20{KN}"
                    "&$select=Id,Name,TypeDesc,StatusID,PersonID,GovMinistryID,SubmitDate,ReplyMinisterDate")
    agendas = paged(PI, "KNS_Agenda",
                    f"$filter=KnessetNum%20eq%20{KN}"
                    "&$select=Id,Name,ClassificationDesc,SubTypeDesc,StatusID,InitiatorPersonID,CommitteeID,GovRecommendationDesc,LastUpdatedDate")
    persons = person_map({q["PersonID"] for q in queries if q.get("PersonID")}
                         | {a["InitiatorPersonID"] for a in agendas if a.get("InitiatorPersonID")})
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
                "date": (a.get("LastUpdatedDate") or "")[:10]})
    print(f"queries: {len(queries)} ({len(queries_by_mk)} MKs), agendas: "
          f"{len(agendas)} ({len(agendas_by_mk)} MKs)", flush=True)

    # ---- bill lineage: unions, splits, names per reading ---------------
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

    # ---- enacted laws: name-matched + רשומות citation ------------------
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

    # ---- secondary legislation per committee ---------------------------
    secondary = paged(PI, "KNS_SecondaryLaw",
                      f"$filter=KnessetNum%20eq%20{KN}"
                      "&$select=Id,Name,StatusName,CompletionCauseDesc,CommitteeID,PublicationDate,CommitteeApprovalDate")
    secondary_by_committee = {}
    for s in sorted(secondary, key=lambda x: x.get("PublicationDate") or "", reverse=True):
        cid = s.get("CommitteeID")
        if cid not in committee_ids:
            continue
        e = secondary_by_committee.setdefault(str(cid), {"total": 0, "items": []})
        e["total"] += 1
        if len(e["items"]) < 10:
            e["items"].append({"name": s["Name"], "status": s.get("StatusName") or "",
                               "cause": s.get("CompletionCauseDesc") or "",
                               "date": (s.get("PublicationDate") or s.get("CommitteeApprovalDate") or "")[:10]})
    print(f"secondary: {len(secondary)} (in {len(secondary_by_committee)} committees)", flush=True)

    # ---- broadcasts + joint composition --------------------------------
    # Broadcast archive rows exist mainly for older sessions; the recent
    # 362 committee sessions have none yet. Intersect with every session
    # id the UI can open: committee recents + all agenda shards.
    all_session_ids = set(session_ids)
    for shard in Path("data/agenda").glob("*.json"):
        try:
            data = json.loads(shard.read_text(encoding="utf-8"))
        except Exception:
            continue
        for key in ("plenum", "committees"):  # shard shape: {month, plenum:[], committees:[]}
            for s in data.get(key, []) if isinstance(data, dict) else []:
                if isinstance(s, dict) and s.get("id"):
                    all_session_ids.add(s["id"])
    broadcasts = {}
    for r in in_batches(PI, "KNS_BroadcastCommitteSession", "Id", all_session_ids,
                        "Id,BroadcastUrl"):
        if r.get("BroadcastUrl"):
            broadcasts[str(r["Id"])] = r["BroadcastUrl"]
    joint = {}
    for r in paged(PI, "KNS_JointCommittee", "$select=CommitteeID,ParticipantCommitteeID"):
        if r["CommitteeID"] in committee_ids and r["ParticipantCommitteeID"] in committee_ids:
            joint.setdefault(str(r["CommitteeID"]), []).append(r["ParticipantCommitteeID"])
    print(f"broadcasts: {len(broadcasts)}, joint maps: {len(joint)}", flush=True)

    # ---- lobbyist register (current; NOT Knesset-scoped) ---------------
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
    print(f"DONE -> data/k25_extras.json")


if __name__ == "__main__":
    main()
