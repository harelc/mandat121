#!/usr/bin/env python3
"""Offline tests for the generalized K26 builder branches.

The live OData feed has no KnessetNum=26 rows yet, so the non-empty K26
paths of the builders can't be exercised against real data. These tests
stub the OData `get()` layer with synthetic rows shaped like the real
entities and assert the builders produce the expected k26_* outputs —
and that the unpublished-layer paths stay clean no-ops.

Run: python3 -m unittest discover -s tests -v   (stdlib only, no network)
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent


def load_module(fname):
    spec = importlib.util.spec_from_file_location(fname[:-3], ROOT / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def first_elected():
    prev = json.loads((ROOT / "data" / "k26_preview.json").read_text("utf-8"))
    party = prev["parties"][0]
    return party["name"], party["elected"][0]


def split_name(full):
    parts = full.split()
    return parts[0], " ".join(parts[1:])


class K26TenureTest(unittest.TestCase):
    def test_tenure_and_factions_from_synthetic_positions(self):
        mod = load_module("build_k25_tenure.py")
        party, name = first_elected()
        last, first = split_name(name)
        person = {"PersonID": 1, "LastName": last, "FirstName": first}

        def fake_get(entity, filt):
            if entity == "KNS_Person":
                return [person]
            if "PositionID eq 54" in filt:
                return [{"PersonID": 1, "StartDate": "2026-11-01", "FinishDate": None,
                         "FactionName": party}]
            return [{"PersonID": 1, "StartDate": "2026-11-01", "FinishDate": None}]

        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            shutil.copy(ROOT / "data" / "k26_preview.json", data / "k26_preview.json")
            mod.DATA, mod.get = data, fake_get
            sys.argv = ["build_k25_tenure.py", "--knesset", "26"]
            mod.main()
            out = json.loads((data / "k26_tenure.json").read_text("utf-8"))
        self.assertEqual(out["tenure"][name], [["2026-11-01", None]])
        self.assertEqual(out["factions"][name], party)


class K26CommitteeMembersTest(unittest.TestCase):
    def test_members_from_synthetic_positions(self):
        mod = load_module("build_k25_committee_members.py")
        party, name = first_elected()
        last, first = split_name(name)
        person = {"PersonID": 7, "LastName": last, "FirstName": first}

        def fake_get(entity, filt, base=None):
            if entity == "KNS_Person":
                return [person]
            return [{"PersonID": 7, "PositionID": 41, "CommitteeID": 99,
                     "CommitteeName": "ועדת הכספים", "StartDate": "2026-11-05",
                     "FinishDate": None}]

        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            shutil.copy(ROOT / "data" / "k26_preview.json", data / "k26_preview.json")
            mod.DATA, mod.get = data, fake_get
            sys.argv = ["build_k25_committee_members.py", "--knesset", "26"]
            mod.main()
            out = json.loads((data / "k26_committee_members.json").read_text("utf-8"))
        self.assertEqual(out["by_mk"][name][0]["role"], "chair")
        self.assertEqual(out["by_committee"]["99"][0]["name"], name)


class K26ListOrderTest(unittest.TestCase):
    def test_ranks_match_preview_order(self):
        mod = load_module("build_k25_list_order.py")
        prev = json.loads((ROOT / "data" / "k26_preview.json").read_text("utf-8"))
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            shutil.copy(ROOT / "data" / "k26_preview.json", data / "k26_preview.json")
            mod.DATA = data
            mod.main_k26()
            out = json.loads((data / "k26_list_order.json").read_text("utf-8"))
        self.assertEqual(len(out["ranks"]), 120)
        top = prev["parties"][0]
        self.assertEqual(out["ranks"][top["elected"][0]]["rank"], 1)
        self.assertEqual(out["ranks"][top["elected"][1]]["rank"], 2)
        # Rank is the original CEC candidate-list position, not the index
        # within `elected` — the two diverge after pre-swearing resignations
        # (e.g. ש״ס 2026: places 1, 6, 7 resigned, so #8 and #9 entered).
        for p in prev["parties"]:
            for name in p["elected"]:
                self.assertEqual(out["ranks"][name]["rank"],
                                 p["candidates"].index(name) + 1)


class K26CommitteesBillIdsTest(unittest.TestCase):
    def test_bill_ids_read_k26_bills_file(self):
        mod = load_module("build_committees.py")
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            (data / "k26_bills.json").write_text(
                json.dumps([{"id": 5}, {"id": 9}, {"name": "no id"}]), "utf-8")
            mod.DATA = data
            self.assertEqual(mod.bill_ids_for(26), [5, 9])
            self.assertEqual(mod.bill_ids_for(26), [5, 9])


class PickDocUrlTest(unittest.TestCase):
    """KNS_DocumentBill holds a bill's נוסח from the day it is tabled —
    the original 'לדיון מוקדם' print (with דברי הסבר) wins, PDF over DOC."""

    def test_prefers_original_pdf_over_doc_and_later_versions(self):
        mod = load_module("build_knesset.py")
        docs = [
            {"Id": 30, "GroupTypeID": 4, "ApplicationDesc": "PDF",
             "FilePath": "https://fs.knesset.gov.il/25\\law\\25_ls2_30.pdf"},
            {"Id": 10, "GroupTypeID": 1, "ApplicationDesc": "DOC",
             "FilePath": "https://fs.knesset.gov.il/25\\law\\25_lst_10.docx"},
            {"Id": 11, "GroupTypeID": 1, "ApplicationDesc": "PDF",
             "FilePath": "https://fs.knesset.gov.il/25\\law\\25_lst_10.pdf"},
            {"Id": 40, "GroupTypeID": 9, "ApplicationDesc": "PDF",
             "FilePath": "https://fs.knesset.gov.il/25\\law\\25_lsr_40.pdf"},
        ]
        self.assertEqual(mod.pick_doc_url(docs),
                         "https://fs.knesset.gov.il/25/law/25_lst_10.pdf")

    def test_gov_bill_without_preliminary_print_uses_first_reading(self):
        mod = load_module("build_knesset.py")
        docs = [{"Id": 20, "GroupTypeID": 2, "ApplicationDesc": "PDF",
                 "FilePath": "https://fs.knesset.gov.il/25\\law\\25_ls1_20.pdf"}]
        self.assertEqual(mod.pick_doc_url(docs),
                         "https://fs.knesset.gov.il/25/law/25_ls1_20.pdf")

    def test_no_docs(self):
        mod = load_module("build_knesset.py")
        self.assertIsNone(mod.pick_doc_url([]))
        self.assertIsNone(mod.pick_doc_url(None))

    def test_build_docs_dedupes_formats_and_orders_stages(self):
        mod = load_module("build_knesset.py")
        docs = [
            {"Id": 5, "GroupTypeID": 9, "GroupTypeDesc": "חוק - פרסום ברשומות",
             "ApplicationDesc": "PDF", "FilePath": "https://fs.knesset.gov.il/25\\law\\25_lsr_5.pdf"},
            {"Id": 2, "GroupTypeID": 1, "GroupTypeDesc": "הצעת חוק לדיון מוקדם",
             "ApplicationDesc": "DOC", "FilePath": "https://fs.knesset.gov.il/25\\law\\25_lst_2.docx"},
            {"Id": 3, "GroupTypeID": 1, "GroupTypeDesc": "הצעת חוק לדיון מוקדם",
             "ApplicationDesc": "PDF", "FilePath": "https://fs.knesset.gov.il/25\\law\\25_lst_2.pdf"},
            {"Id": 4, "GroupTypeID": 59, "GroupTypeDesc": "חומר רקע",
             "ApplicationDesc": "PDF", "FilePath": "https://fs.knesset.gov.il/25\\law\\bg_4.pdf"},
            {"Id": 6, "GroupTypeID": 1, "GroupTypeDesc": "הצעת חוק לדיון מוקדם",
             "ApplicationDesc": "PDF", "FilePath": "https://fs.knesset.gov.il/25\\law\\25_lst_2.pdf"},
        ]
        out = mod.build_docs(docs)
        self.assertEqual([d["group_id"] for d in out], [1, 9, 59])
        self.assertEqual(out[0]["format"], "PDF")
        self.assertEqual(out[0]["url"], "https://fs.knesset.gov.il/25/law/25_lst_2.pdf")
        self.assertEqual(mod.build_docs([]), [])


class WebsiteVoteFallbackTest(unittest.TestCase):
    """Non-electronic votes have no OData tally (KNS_PlenumVoteResult
    holds rows only for electronic/roll-call votes), so build_knesset
    falls back to WebSiteApi GetVoteDetails — counters there omit zero
    categories, and secret ballots report candidates, not for/against."""

    def parse(self, detail):
        return load_module("build_knesset.py").parse_website_vote(detail)

    def test_hand_vote_counters_default_missing_titles_to_zero(self):
        out = self.parse({
            "VoteHeader": [{"IsForAccepted": True}],
            "VoteCounters": [{"Title": "בעד", "countOfResult": 6}],
            "VoteDetails": [], "DescreetVoteResults": []})
        self.assertEqual(out["tally"], {"for": 6, "against": 0, "abstain": 0})
        self.assertIsNone(out["secret"])
        self.assertTrue(out["accepted"])

    def test_present_not_voting_is_not_a_tally(self):
        out = self.parse({
            "VoteHeader": [{"IsForAccepted": True}],
            "VoteCounters": [{"Title": "בעד", "countOfResult": 28},
                             {"Title": "נוכח ולא הצביע", "countOfResult": 4}],
            "VoteDetails": [], "DescreetVoteResults": []})
        self.assertEqual(out["tally"], {"for": 28, "against": 0, "abstain": 0})

    def test_secret_ballot_reports_candidates_not_for_against(self):
        out = self.parse({
            "VoteHeader": [{"IsForAccepted": False}],
            "VoteCounters": [],
            "VoteDetails": [],
            "DescreetVoteResults": [
                {"description": "מועמד א", "AmountFor": 57},
                {"description": "מועמד ב", "AmountFor": 60}]})
        self.assertIsNone(out["tally"])
        self.assertEqual(out["secret"],
                         [{"name": "מועמד א", "votes": 57},
                          {"name": "מועמד ב", "votes": 60}])
        self.assertFalse(out["accepted"])

    def test_uncounted_hand_vote_carries_only_the_verdict(self):
        out = self.parse({
            "VoteHeader": [{"IsForAccepted": True}],
            "VoteCounters": [], "VoteDetails": [], "DescreetVoteResults": []})
        self.assertIsNone(out["tally"])
        self.assertTrue(out["accepted"])

    def test_empty_payload(self):
        out = self.parse(None)
        self.assertEqual(out, {"tally": None, "secret": None, "accepted": None})


class UnpublishedLayerNoOpTest(unittest.TestCase):
    """Builders run from a cwd with no K26 data must exit 0 and write nothing."""

    def run_builder(self, script, td):
        return subprocess.run(
            [sys.executable, str(ROOT / script), "--knesset", "26"],
            cwd=td, capture_output=True, text=True, timeout=120)

    def test_vote_results_noop_without_votes_file(self):
        with tempfile.TemporaryDirectory() as td:
            r = self.run_builder("build_k25_vote_results.py", td)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((Path(td) / "data" / "k26_vote_results.json").exists())

    def test_vote_results_noop_with_empty_votes(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "data").mkdir()
            (Path(td) / "data" / "k26_votes.json").write_text("[]", "utf-8")
            r = self.run_builder("build_k25_vote_results.py", td)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((Path(td) / "data" / "k26_vote_results.json").exists())

    def test_protocols_noop_without_committees_file(self):
        with tempfile.TemporaryDirectory() as td:
            r = self.run_builder("build_protocols.py", td)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((Path(td) / "data" / "k26_protocols_index.json").exists())


if __name__ == "__main__":
    unittest.main()
