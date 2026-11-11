#!/usr/bin/env python3
"""Offline tests for meaningful_diff.py — the daily-commit gate that
keeps timestamp-only data churn from triggering a Netlify deploy.

Run: python3 -m unittest discover -s tests -v   (stdlib only, no network)
"""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("meaningful_diff", ROOT / "meaningful_diff.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def enc(o):
    return json.dumps(o, ensure_ascii=False).encode("utf-8")


class MeaningfulDiffTest(unittest.TestCase):
    def test_generated_at_only_is_noise(self):
        old = enc({"generated_at": "2026-11-11T09:02:48+02:00", "committees": [{"id": 1}]})
        new = enc({"generated_at": "2026-11-11T12:30:01+02:00", "committees": [{"id": 1}]})
        self.assertFalse(mod.is_meaningful(old, new))

    def test_fetched_at_nested_in_meta_is_noise(self):
        old = enc({"_meta": {"fetched_at": "2026-11-10T04:30:00+00:00", "n": 120}, "by_name": {}})
        new = enc({"_meta": {"fetched_at": "2026-11-11T04:30:00+00:00", "n": 120}, "by_name": {}})
        self.assertFalse(mod.is_meaningful(old, new))

    def test_real_change_is_meaningful(self):
        old = enc({"generated_at": "t1", "committees": [{"id": 1, "sessions_total": 5}]})
        new = enc({"generated_at": "t2", "committees": [{"id": 1, "sessions_total": 6}]})
        self.assertTrue(mod.is_meaningful(old, new))

    def test_key_order_churn_is_noise(self):
        old = b'{"a": 1, "b": {"x": [1, 2], "y": 2}}'
        new = b'{"b": {"y": 2, "x": [1, 2]}, "a": 1}'
        self.assertFalse(mod.is_meaningful(old, new))

    def test_volatile_key_change_hiding_real_change_still_meaningful(self):
        old = enc({"_meta": {"fetched_at": "t1"}, "lobbyists": [{"name": "a"}]})
        new = enc({"_meta": {"fetched_at": "t2"}, "lobbyists": [{"name": "b"}]})
        self.assertTrue(mod.is_meaningful(old, new))

    def test_added_or_deleted_is_meaningful(self):
        self.assertTrue(mod.is_meaningful(None, b"{}"))
        self.assertTrue(mod.is_meaningful(b"{}", None))

    def test_identical_bytes_are_noise(self):
        self.assertFalse(mod.is_meaningful(b"abc", b"abc"))

    def test_non_json_byte_change_is_meaningful(self):
        self.assertTrue(mod.is_meaningful(b"\xff\x00a", b"\xff\x00b"))


if __name__ == "__main__":
    unittest.main()
