"""Committee speaking-participation matrix (Knesset 25).

Honest proxy, NOT an official attendance register: the Knesset does not
publish per-member committee attendance. What the published protocols do
show is who *spoke*. A member who attended silently will not be marked —
the UI must say so wherever this data is shown.

Sources (already in the repo, no new scraping):
  data/cmt_hl.json              per-MK session appearances (sid, turns, words)
                                + session -> [committee name, date, doc url]
  data/k25_committee_members.json  committee rosters with tenure dates

Output: data/committee_attendance.json
  { "_meta": {...},
    "by_committee": { "<committee_id>": {
        "members": [name, ...],            # latest roster, chairs first
        "sessions": [{"id","date"}, ...],  # last N sessions, newest first
        "spoke": { name: [session_index, ...] } } } }
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent
N_SESSIONS = 24


def main() -> None:
    hl = json.loads((ROOT / "data/cmt_hl.json").read_text(encoding="utf-8"))
    mem = json.loads((ROOT / "data/k25_committee_members.json").read_text(encoding="utf-8"))
    sessions_meta = hl["sessions"]  # sid -> [committee, date, url]

    # sid -> set of MK names who spoke there
    spoke_in: dict[str, set[str]] = {}
    for mk, rec in hl["by_mk"].items():
        for sid, _turns, _words in rec.get("apps", []):
            spoke_in.setdefault(sid, set()).add(mk)

    # Last-known roster, mirroring cmtRoster() in index.html exactly:
    # stints in force at the committee's last session (the official DB
    # retroactively closed every K25 stint, so "no end date" means
    # nothing for K25); fallback to the latest-starting stints.
    cmt_meta = {
        str(c["id"]): c
        for c in json.loads((ROOT / "data/committees.json").read_text(encoding="utf-8"))["committees"]
    }

    def last_known_roster(cid: str, roster: list[dict]) -> list[dict]:
        as_of = (cmt_meta.get(cid) or {}).get("last_session") or max(
            (r.get("to") or r.get("from") or "") for r in roster
        )
        if not as_of:
            return []
        covers = [
            r for r in roster
            if (r.get("from") or "") <= as_of and (not r.get("to") or r["to"] >= as_of)
        ]
        if not covers:
            latest_from = max((r.get("from") or "") for r in roster)
            covers = [r for r in roster if r.get("from") == latest_from]
        rank = {"chair": 0, "member": 1, "substitute": 2}
        seen, deduped = set(), []
        for r in sorted(covers, key=lambda r: (rank.get(r["role"], 1), r["name"])):
            if r["name"] not in seen:
                seen.add(r["name"])
                deduped.append(r)
        return deduped

    out = {}
    for cid, roster in mem["by_committee"].items():
        if not roster:
            continue
        cname = roster[0]["committee"]
        names = [r["name"] for r in last_known_roster(str(cid), roster)]
        if not names:
            continue
        sids = [
            (sid, meta[1])
            for sid, meta in sessions_meta.items()
            if meta[0] == cname and sid in spoke_in
        ]
        sids.sort(key=lambda x: x[1], reverse=True)
        sids = sids[:N_SESSIONS]
        if not sids:
            continue
        spoke = {}
        for i, (sid, _d) in enumerate(sids):
            for mk in spoke_in[sid] & set(names):
                spoke.setdefault(mk, []).append(i)
        out[str(cid)] = {
            "as_of": (cmt_meta.get(str(cid)) or {}).get("last_session"),
            "members": names,
            "sessions": [{"id": sid, "date": d} for sid, d in sids],
            "spoke": spoke,
        }

    payload = {
        "_meta": {
            "method": "Speaking turns in published committee protocols "
                      "(cmt_hl speaker stats). Not an official attendance "
                      "register; silent attendees are not marked.",
            "sessions_per_committee": N_SESSIONS,
            "committees": len(out),
        },
        "by_committee": out,
    }
    p = ROOT / "data/committee_attendance.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {p.name}: {len(out)} committees")


if __name__ == "__main__":
    main()
