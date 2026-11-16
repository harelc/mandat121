#!/usr/bin/env python3
"""Offline tests for summarize_commit.py — the per-commit Markdown
summary posted (as an auto-closed issue) after knesset-daily pushes.

These spec `semantic_detail`, the JSON-diff helper behind the file
table. (They originally spec'd the old `describe_change` string API;
the e3045de rewrite replaced it and these were updated to match —
same scenarios, current return format.)

Run: python3 -m unittest discover -s tests -v   (stdlib only, no network)
"""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("summarize_commit", ROOT / "summarize_commit.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def enc(o):
    return json.dumps(o, ensure_ascii=False).encode("utf-8")


class SemanticDetailTest(unittest.TestCase):
    def test_list_growth(self):
        self.assertEqual(
            mod.semantic_detail(enc([1, 2]), enc([1, 2, 3])),
            ["+1: 3"])

    def test_list_same_length_contents_changed(self):
        self.assertEqual(
            mod.semantic_detail(enc([1]), enc([2])),
            ["+1: 2", "−1: 1"])

    def test_dict_reports_only_changed_collection_sizes(self):
        old = enc({"bills": [1, 2], "votes": [1], "_meta": {"fetched_at": "t1"}})
        new = enc({"bills": [1, 2, 3], "votes": [1], "_meta": {"fetched_at": "t2"}})
        self.assertEqual(mod.semantic_detail(old, new), ["bills 2 → 3 · +3"])

    def test_volatile_keys_alone_read_as_contents_changed_not_counts(self):
        old = enc({"generated_at": "t1", "days": []})
        new = enc({"generated_at": "t2", "days": []})
        self.assertEqual(mod.semantic_detail(old, new), ["contents changed"])

    def test_added_and_deleted(self):
        self.assertEqual(mod.semantic_detail(None, b"{}"), ["new file"])
        self.assertEqual(mod.semantic_detail(b"{}", None), ["deleted"])

    def test_non_json_has_no_semantic_detail(self):
        self.assertEqual(mod.semantic_detail(b"a", b"b"), [])


if __name__ == "__main__":
    unittest.main()
