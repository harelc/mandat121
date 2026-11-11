#!/usr/bin/env python3
"""Offline tests for the incremental / probe-gated daily builders.

The daily job used to take 20+ minutes because build_committees,
build_odata_extras and build_protocols refetched and rebuilt
everything even when the Knesset had published nothing. These tests
stub the network layer with synthetic rows and assert:

  * build_committees incremental path merges a changed/new session
    window over the previous output + session index, without touching
    carried-over sessions, and maintains per-committee totals.
  * build_protocols reuses parsed protocols whose source URL is
    unchanged instead of downloading them again.
  * build_odata_extras reuses a section verbatim when its change
    probe (newest LastUpdatedDate / row count / input fingerprint)
    has not moved, and rebuilds when it has.

Run: python3 -m unittest discover -s tests -v   (stdlib only, no network)
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent.parent


def load_module(fname):
    spec = importlib.util.spec_from_file_location(fname[:-3], ROOT / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Cwd:
    def __enter__(self):
        self.old = os.getcwd()
        os.chdir(self.td)
        return self

    def __init__(self, td):
        self.td = td

    def __exit__(self, *a):
        os.chdir(self.old)


SESSION = lambda sid, start, cid=7: {  # noqa: E731
    "Id": sid, "CommitteeID": cid, "StartDate": start,
    "TypeDesc": "פתוחה", "StatusDesc": "פעילה", "Location": "אולם"}


class CommitteesIncrementalTest(unittest.TestCase):
    def test_window_merge_over_previous_output(self):
        mod = load_module("build_committees.py")
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            data.mkdir()
            mod.DATA = data
            (data / "bills.json").write_text(json.dumps(
                [{"bill_id": 100, "knesset_num": 25}]), "utf-8")
            prev = {
                "generated_from": "x", "knesset": 25, "recent_since": "2026-06-24",
                "status_map": {"5": "סטטוס"},
                "committees": [{"id": 7, "name": "ועדה", "sessions_total": 3}],
                "bill_committees": {"100": [
                    {"date": "2026-07-01", "session_id": 1, "committee_id": 7,
                     "status_id": 5, "status": "ישן", "type": "פתוחה"},
                    {"date": "2026-07-05", "session_id": 2, "committee_id": 7,
                     "status_id": 5, "status": "ישן", "type": "פתוחה"},
                ]},
                "recent_sessions": [
                    {"id": 2, "committee_id": 7, "committee": "ועדה",
                     "start": "2026-07-05T10:00:00+03:00", "type": "פתוחה",
                     "status": "פעילה", "location": "אולם",
                     "items": [{"item_id": 100, "name": "הצעה", "item_type": 2,
                                "status_id": 5, "status": "ישן", "in_dataset": True}],
                     "docs": []},
                    {"id": 1, "committee_id": 7, "committee": "ועדה",
                     "start": "2026-07-01T10:00:00+03:00", "type": "פתוחה",
                     "status": "פעילה", "location": "אולם",
                     "items": [], "docs": [{"group": "פרוטוקול", "path": "p1"}]},
                ],
            }
            (data / "committees.json").write_text(json.dumps(prev, ensure_ascii=False), "utf-8")
            state = {
                "sessions": {
                    "1": [7, "2026-07-01T10:00:00+03:00", "פתוחה", "פעילה", "אולם"],
                    "2": [7, "2026-07-05T10:00:00+03:00", "פתוחה", "פעילה", "אולם"],
                    "3": [7, "2026-05-01T10:00:00+03:00", "פתוחה", "פעילה", "אולם"],
                },
                "watermark": "2026-07-05T10:00:00+03:00",
                "bills_seen": [100],
            }
            (data / "committees_state.json").write_text(json.dumps(state), "utf-8")

            def fake_fetch_all(entity, params, page=100):
                if entity == "KNS_Committee":
                    return [{"Id": 7, "Name": "ועדה", "CategoryDesc": "c",
                             "CommitteeTypeDesc": "t", "IsCurrent": True}]
                if entity == "KNS_Status":
                    return [{"Id": 5, "Desc": "סטטוס"}]
                if entity == "KNS_CommitteeSession":  # overlap window
                    return [SESSION(2, "2026-07-05T10:00:00+03:00"),
                            SESSION(4, "2026-07-09T10:00:00+03:00")]
                raise AssertionError(f"unexpected fetch_all {entity}")

            def fake_fetch_in(entity, field, ids, extra, select, batch=15):
                if entity == "KNS_CmtSessionItem":
                    return [
                        {"CommitteeSessionID": 2, "ItemID": 100, "Name": "הצעה",
                         "ItemTypeID": 2, "StatusID": 5},
                        {"CommitteeSessionID": 4, "ItemID": 100, "Name": "הצעה",
                         "ItemTypeID": 2, "StatusID": 5},
                        {"CommitteeSessionID": 4, "ItemID": 999, "Name": "אחר",
                         "ItemTypeID": 6000, "StatusID": None},
                    ]
                if entity == "KNS_DocumentCommitteeSession":
                    return [{"CommitteeSessionID": 4, "GroupTypeDesc": "פרוטוקול",
                             "FilePath": "p4"}]
                raise AssertionError(f"unexpected fetch_in {entity}")

            mod.fetch_all, mod.fetch_in = fake_fetch_all, fake_fetch_in
            with mock.patch.object(sys, "argv", ["build_committees.py", "--knesset", "25"]):
                mod.main()

            out = json.loads((data / "committees.json").read_text("utf-8"))
            cmt = out["committees"][0]
            self.assertEqual(cmt["sessions_total"], 4)       # index 1,2,3 + new 4
            self.assertEqual(cmt["last_session"], "2026-07-09")
            entries = out["bill_committees"]["100"]
            self.assertEqual([e["session_id"] for e in entries], [1, 2, 4])
            # session 2 was in the changed window: its entry is refreshed
            self.assertEqual(entries[1]["status"], "סטטוס")
            recent = out["recent_sessions"]
            self.assertEqual([s["id"] for s in recent], [4, 2, 1])
            self.assertEqual(recent[0]["docs"], [{"group": "פרוטוקול", "path": "p4"}])
            # session 1 was carried over untouched (docs kept, no refetch)
            self.assertEqual(recent[2]["docs"], [{"group": "פרוטוקול", "path": "p1"}])
            new_state = json.loads((data / "committees_state.json").read_text("utf-8"))
            self.assertTrue(new_state["watermark"].startswith("2026-07-09"))


class ProtocolsSkipTest(unittest.TestCase):
    def _fixture(self, td, url):
        data = Path(td) / "data"
        (data / "protocols").mkdir(parents=True)
        (data / "committees.json").write_text(json.dumps({
            "recent_sessions": [{"id": 55, "committee": "ועדה", "start": "2026-01-01",
                                 "docs": [{"group": "פרוטוקול ועדה", "path": url}]}],
        }, ensure_ascii=False), "utf-8")
        (data / "protocols" / "55.json").write_text("{}", "utf-8")
        (data / "protocols_index.json").write_text(
            json.dumps({"55": {"turns": 3, "speakers": 2}}), "utf-8")
        (data / "protocols_state.json").write_text(json.dumps({"55": url}), "utf-8")

    def test_unchanged_protocol_is_not_redownloaded(self):
        mod = load_module("build_protocols.py")
        with tempfile.TemporaryDirectory() as td, Cwd(td):
            self._fixture(td, "http://fs/55.docx")
            mod.parse = mock.Mock(side_effect=AssertionError("network!"))
            with mock.patch.object(sys, "argv", ["build_protocols.py", "--knesset", "25"]):
                mod.main()
            idx = json.loads((Path(td) / "data" / "protocols_index.json").read_text("utf-8"))
            self.assertEqual(idx, {"55": {"turns": 3, "speakers": 2}})

    def test_changed_url_is_redownloaded(self):
        mod = load_module("build_protocols.py")
        with tempfile.TemporaryDirectory() as td, Cwd(td):
            self._fixture(td, "http://fs/55.docx")
            (Path(td) / "data" / "protocols_state.json").write_text(
                json.dumps({"55": "http://fs/OLD.docx"}), "utf-8")
            mod.parse = lambda s, u: {"id": s["id"], "committee": "", "date": "2026-01-01",
                                      "speakers": {"a": {}}, "turns": [1, 2, 3, 4]}
            with mock.patch.object(sys, "argv", ["build_protocols.py", "--knesset", "25"]):
                mod.main()
            idx = json.loads((Path(td) / "data" / "protocols_index.json").read_text("utf-8"))
            self.assertEqual(idx["55"]["turns"], 4)


class ExtrasProbeTest(unittest.TestCase):
    SECTIONS = ["queries_by_mk", "agendas_by_mk", "bill_links", "laws",
                "secondary_by_committee", "broadcasts", "joint", "lobbyists"]

    def _fixture(self, td):
        data = Path(td) / "data"
        data.mkdir()
        (data / "k25_bills.json").write_text(json.dumps(
            [{"id": 1, "name": "חוק", "publication_date": None}]), "utf-8")
        (data / "committees.json").write_text(json.dumps(
            {"committees": [{"id": 7, "name": "ועדה"}], "recent_sessions": []}), "utf-8")

    def _fake_get(self, base, path):
        if "$select=LastUpdatedDate" in path:
            return {"value": [{"LastUpdatedDate": "2026-01-01T00:00:00"}]}
        if "$count=true" in path:
            return {"@odata.count": 3}
        if "KNS_Status" in path:
            return {"value": [{"Id": 1, "Desc": "x"}]}
        if "KNS_GovMinistry" in path:
            return {"value": []}
        raise AssertionError(f"unexpected network get: {path}")

    def test_sections_reused_when_probes_unchanged(self):
        mod = load_module("build_odata_extras.py")
        with tempfile.TemporaryDirectory() as td, Cwd(td):
            self._fixture(td)
            mod.get = self._fake_get
            mod.paged = lambda base, entity, params: (
                [{"Id": 1, "Desc": "x"}] if entity == "KNS_Status" else [])
            sentinels = {s: (["L"] if s == "lobbyists" else {s: 1}) for s in self.SECTIONS}
            for name, val in sentinels.items():
                fn = {"queries_by_mk": "build_queries", "agendas_by_mk": "build_agendas",
                      "bill_links": "build_links", "laws": "build_laws",
                      "secondary_by_committee": "build_secondary",
                      "broadcasts": "build_broadcasts", "joint": "build_joint",
                      "lobbyists": "build_lobbyists"}[name]
                setattr(mod, fn, lambda *a, v=val: v)
            mod.main()  # first run: no state -> builds, writes probes
            out1 = json.loads((Path(td) / "data" / "k25_extras.json").read_text("utf-8"))
            for s in self.SECTIONS:
                self.assertEqual(out1[s], sentinels[s])
            # second run: probes unchanged -> no section builder may run
            for fn in ["build_queries", "build_agendas", "build_links", "build_laws",
                       "build_secondary", "build_broadcasts", "build_joint", "build_lobbyists"]:
                setattr(mod, fn, mock.Mock(side_effect=AssertionError(f"{fn} ran!")))
            mod.main()
            out2 = json.loads((Path(td) / "data" / "k25_extras.json").read_text("utf-8"))
            self.assertEqual(out1, out2)


class VoteResultsResumeTest(unittest.TestCase):
    """Regression: a checkpointed vote with exactly 100 rows is treated
    as possibly-truncated, deleted and refetched on resume. Tallies must
    be computed from the final rows only — the old incremental tally
    accumulation counted such votes twice (49 votes shipped doubled,
    e.g. 184 against out of 120 MKs, on 2026-11-10)."""

    def test_refetched_100_row_vote_is_not_counted_twice(self):
        mod = load_module("build_k25_vote_results.py")
        with tempfile.TemporaryDirectory() as td, Cwd(td):
            data = Path(td) / "data"
            data.mkdir()
            (data / "k25_votes.json").write_text(json.dumps(
                [{"id": 1, "for": None, "against": None, "abstain": None}]), "utf-8")
            (data / "k25_vote_results.json").write_text(json.dumps(
                {"names": ["a"], "votes": {"1": [[0, 1]] * 100}}), "utf-8")
            mod.fetch_vote = lambda vid: (vid, [
                {"ResultDesc": "בעד", "LastName": "a", "FirstName": ""}] * 100)
            with mock.patch.object(sys, "argv", ["build_k25_vote_results.py"]):
                mod.main()
            votes = json.loads((data / "k25_votes.json").read_text("utf-8"))
            self.assertEqual(votes[0]["for"], 100)
            results = json.loads((data / "k25_vote_results.json").read_text("utf-8"))
            self.assertEqual(len(results["votes"]["1"]), 100)


if __name__ == "__main__":
    unittest.main()
