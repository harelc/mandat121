#!/usr/bin/env python3
"""Regression: same-name MKs in the MkLobby previous-MKs API.

The API returns one row per (person, Knesset term), and distinct people
share names: כהן אלי is MkId 714 (b. 1949, Knesset 15 only) and MkId 928
(b. 1972, Knessets 20-26). The builder used to keep the first name match,
which downloaded the 714 portrait into the K25 leaver's file
(data/photos/mk_ca0825b2.jpg) on mandat121. Matching must prefer the
person who actually served in Knesset 25, regardless of API row order.

Run: python3 -m unittest discover -s tests -v   (stdlib only, no network)
"""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent


def load_module():
    spec = importlib.util.spec_from_file_location(
        "build_k25_photos_official", ROOT / "build_k25_photos_official.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def eli_cohen_rows():
    base = {"LastName": "כהן", "FirstName": "אלי"}
    return [
        {**base, "MkId": 714, "Knesset": 15,
         "ImgPath": "https://fs.knesset.gov.il/globaldocs/MK/714/1_714_3_1565.jpeg"},
        {**base, "MkId": 928, "Knesset": 20,
         "ImgPath": "https://fs.knesset.gov.il/globaldocs/MK/928/1_928_3_2473.jpeg"},
        {**base, "MkId": 928, "Knesset": 25,
         "ImgPath": "https://fs.knesset.gov.il/globaldocs/MK/928/1_928_3_2473.jpeg"},
    ]


class PickEliCohenTest(unittest.TestCase):
    def test_k25_person_wins_regardless_of_row_order(self):
        mod = load_module()
        for rows in (eli_cohen_rows(), eli_cohen_rows()[::-1]):
            by_key, by_norm = mod.index_rows(rows)
            row = mod.find_row(by_key, by_norm, "כהן אלי")
            self.assertEqual(row["MkId"], 928)
            self.assertIn("/MK/928/", row["ImgPath"])

    def test_falls_back_to_most_recent_term_without_k25_row(self):
        mod = load_module()
        rows = [r for r in eli_cohen_rows() if r["Knesset"] != 25]
        by_key, by_norm = mod.index_rows(rows)
        self.assertEqual(mod.find_row(by_key, by_norm, "כהן אלי")["MkId"], 928)

    def test_no_match_returns_none(self):
        mod = load_module()
        by_key, by_norm = mod.index_rows(eli_cohen_rows())
        self.assertIsNone(mod.find_row(by_key, by_norm, "לוי דוד"))


class ManifestTest(unittest.TestCase):
    def test_eli_cohen_manifest_points_at_mk_928(self):
        man = json.loads((ROOT / "data" / "k25_official_photos.json")
                         .read_text("utf-8"))["photos"]
        entry = man["כהן אלי"]
        self.assertEqual(entry["mk_site_id"], 928)
        self.assertEqual(
            entry["source"],
            "https://fs.knesset.gov.il/globaldocs/MK/928/1_928_3_2473.jpeg")
        self.assertTrue((ROOT / entry["file"]).exists())


if __name__ == "__main__":
    unittest.main()
