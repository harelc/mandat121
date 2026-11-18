#!/usr/bin/env python3
"""Offline tests for build_events.py — the server-side event stream
behind the Mine feed. Runs main() against a temp DATA dir: the real
data/events.json and events_state.json are never touched.

Run: python3 -m unittest discover -s tests -v   (stdlib only, no network)
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("build_events", ROOT / "build_events.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

BILL = {"id": 1, "name": "הצעת חוק הבדיקה", "status_id": 108, "status": "הכנה לקריאה ראשונה"}
DOC1 = {"group": "הצעת חוק לדיון מוקדם", "format": "PDF",
        "url": "https://fs.knesset.gov.il/26/law/26_ls1_111.pdf"}
DOC2 = {"group": "הצעת חוק לקריאה הראשונה", "format": "PDF",
        "url": "https://fs.knesset.gov.il/26/law/26_ls1_222.pdf"}


class BuildEventsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.data = d
        mod.DATA = d
        mod.STATE_F = d / "events_state.json"
        mod.EVENTS_F = d / "events.json"
        (d / "k26_bills.json").write_text(json.dumps([BILL]), "utf-8")
        (d / "k26_votes.json").write_text("[]", "utf-8")
        (d / "k26_committees.json").write_text("{}", "utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def write_docs(self, docs):
        (self.data / "k26_bill_docs.json").write_text(
            json.dumps({"1": docs}, ensure_ascii=False), "utf-8")

    def events(self):
        return json.loads((self.data / "events.json").read_text("utf-8"))

    def test_first_run_seeds_silently(self):
        self.write_docs([DOC1])
        mod.main()
        self.assertEqual(self.events(), [])

    def test_new_doc_fires_once_with_group_label(self):
        self.write_docs([DOC1])
        mod.main()  # baseline
        self.write_docs([DOC1, DOC2])
        mod.main()
        ev = [e for e in self.events() if e["type"] == "new_bill_doc"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["bill_id"], "1")
        self.assertEqual(ev[0]["docs"], ["הצעת חוק לקריאה הראשונה"])
        mod.main()  # no further change -> no duplicate
        ev = [e for e in self.events() if e["type"] == "new_bill_doc"]
        self.assertEqual(len(ev), 1)

    def test_legacy_state_without_docs_seeds_docs_silently(self):
        # A state file from before docs tracking must not flood events
        # for the whole historical corpus on its first docs run.
        self.write_docs([DOC1])
        mod.main()
        state = json.loads((self.data / "events_state.json").read_text("utf-8"))
        del state["docs"]
        (self.data / "events_state.json").write_text(json.dumps(state), "utf-8")
        self.write_docs([DOC1, DOC2])
        mod.main()
        ev = [e for e in self.events() if e["type"] == "new_bill_doc"]
        self.assertEqual(ev, [])


if __name__ == "__main__":
    unittest.main()
