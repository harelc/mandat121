#!/usr/bin/env python3
"""Offline tests for upload_search_shards.py's selection + shrink guard.

Context: the committed data/search_c is a 268-sitting fallback, while
the R2 bucket serves the full backfilled corpus (9,371 sittings) and
the client reads R2 first. Uploading the checkout verbatim used to
repoint live search at the fallback. The guard refuses any selected
corpus that is smaller than what the bucket already serves, and a
subset selection preserves the other corpora's manifest entries.

Run: python3 -m unittest discover -s tests -v   (stdlib only, no network)
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("upload_search_shards", ROOT / "upload_search_shards.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ParseSelectionTest(unittest.TestCase):
    def test_default_is_all_corpora(self):
        sel, force = mod.parse_selection([], mod.CORPORA)
        self.assertEqual(sel, set(mod.CORPORA))
        self.assertFalse(force)

    def test_explicit_subset(self):
        sel, _ = mod.parse_selection(["--corpora", "c26,p26"], mod.CORPORA)
        self.assertEqual(sel, {"c26", "p26"})
        sel, _ = mod.parse_selection(["--corpora=c"], mod.CORPORA)
        self.assertEqual(sel, {"c"})

    def test_force_flag(self):
        _, force = mod.parse_selection(["--corpora", "c", "--force"], mod.CORPORA)
        self.assertTrue(force)


class IsShrinkTest(unittest.TestCase):
    OLD = {"version": "v1", "shards": {"aa.json": ["h1", 1000], "ab.json": ["h2", 1000]}}

    def test_no_bucket_entry_is_never_shrink(self):
        self.assertFalse(mod.is_shrink(None, 0, 0))
        self.assertFalse(mod.is_shrink({}, 0, 0))
        self.assertFalse(mod.is_shrink({"shards": {}}, 0, 0))

    def test_fewer_shards_is_shrink(self):
        self.assertTrue(mod.is_shrink(self.OLD, 1, 5000))

    def test_materially_fewer_bytes_is_shrink(self):
        self.assertTrue(mod.is_shrink(self.OLD, 2, 1000))

    def test_growth_and_same_size_are_fine(self):
        self.assertFalse(mod.is_shrink(self.OLD, 3, 3000))
        self.assertFalse(mod.is_shrink(self.OLD, 2, 2000))


class ShardsToUploadTest(unittest.TestCase):
    FILES = {"aa.json": "h1", "ab.json": "h2-new", "ac.json": "h3"}

    def test_version_change_uploads_every_shard(self):
        # New prefix is empty in the bucket — unchanged shards included,
        # or the manifest flip + stale-generation cleanup orphans them.
        old = {"version": "v1", "shards": {"aa.json": ["h1", 10], "ab.json": ["h2", 10]}}
        self.assertEqual(
            mod.shards_to_upload(self.FILES, old, "v2"),
            ["aa.json", "ab.json", "ac.json"],
        )

    def test_no_bucket_entry_uploads_every_shard(self):
        self.assertEqual(
            mod.shards_to_upload(self.FILES, None, "v1"),
            ["aa.json", "ab.json", "ac.json"],
        )
        self.assertEqual(
            mod.shards_to_upload(self.FILES, {}, "v1"),
            ["aa.json", "ab.json", "ac.json"],
        )

    def test_same_version_uploads_only_changed(self):
        old = {"version": "v1", "shards": {"aa.json": ["h1", 10], "ab.json": ["h2", 10]}}
        self.assertEqual(
            mod.shards_to_upload(self.FILES, old, "v1"),
            ["ab.json", "ac.json"],
        )


if __name__ == "__main__":
    unittest.main()
