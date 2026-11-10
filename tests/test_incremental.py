#!/usr/bin/env python3
"""Offline tests for the incremental (watermark/window) builder paths.

Run: python3 -m unittest discover -s tests -v   (stdlib only, no network)
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent


def load_module(fname):
    spec = importlib.util.spec_from_file_location(fname[:-3], ROOT / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class AgendaLoadExistingTest(unittest.TestCase):
    def test_merges_agenda_and_shards_by_id(self):
        mod = load_module("build_agenda.py")
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            (data / "agenda").mkdir()
            (data / "agenda.json").write_text(json.dumps({
                "plenum": [{"id": 1, "start": "2026-11-02"}],
                "committees": [{"id": 10, "start": "2026-11-02"}],
            }), "utf-8")
            (data / "agenda" / "2026-07.json").write_text(json.dumps({
                "month": "2026-07",
                "plenum": [{"id": 2, "start": "2026-07-20"}],
                "committees": [{"id": 11, "start": "2026-07-20"}],
            }), "utf-8")
            mod.DATA = data
            out = mod.load_existing()
        self.assertEqual(sorted(out["plenum"]), [1, 2])
        self.assertEqual(sorted(out["committees"]), [10, 11])

    def test_missing_files_are_empty_not_fatal(self):
        mod = load_module("build_agenda.py")
        with tempfile.TemporaryDirectory() as td:
            mod.DATA = Path(td)
            self.assertEqual(mod.load_existing(),
                             {"plenum": {}, "committees": {}})


if __name__ == "__main__":
    unittest.main()
