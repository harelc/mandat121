#!/usr/bin/env python3
"""Offline tests for build_mk_highlights.py speaker resolution.

Protocol speakers are given-first, MK keys surname-first; a wrong join
would put one MK's words on another's page, so the mapping (disp
inversion, FORWARD multi-token surnames, middle-name fallback,
ambiguity refusal) is pinned here. Run: python3 -m unittest discover -s tests
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent


def load_module(fname):
    spec = importlib.util.spec_from_file_location(fname[:-3], ROOT / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mh = load_module("build_mk_highlights.py")
NAMES = {"רוטמן שמחה", "פרקש הכהן אורית", "בן גביר איתמר", "וולדיגר מיכל",
         "כהן אלי", "כהן אבי", "בן צור יואב", "בן שטרית רפאל"}
BY_DISPLAY, BY_PAIR = mh.speaker_map(NAMES)


class ResolveTest(unittest.TestCase):
    def test_plain_given_first(self):
        self.assertEqual(mh.resolve("שמחה רוטמן", BY_DISPLAY, BY_PAIR), "רוטמן שמחה")

    def test_multi_token_surname_via_forward(self):
        self.assertEqual(mh.resolve("אורית פרקש הכהן", BY_DISPLAY, BY_PAIR), "פרקש הכהן אורית")
        self.assertEqual(mh.resolve("איתמר בן גביר", BY_DISPLAY, BY_PAIR), "בן גביר איתמר")
        # בן צור is a two-token surname; the naive flip reads צור יואב בן.
        self.assertEqual(mh.disp("בן צור יואב"), "יואב בן צור")
        self.assertEqual(mh.resolve("יואב בן צור", BY_DISPLAY, BY_PAIR), "בן צור יואב")

    def test_particle_surname_without_forward_entry(self):
        self.assertEqual(mh.disp("בן שטרית רפאל"), "רפאל בן שטרית")
        self.assertEqual(mh.resolve("רפאל בן שטרית", BY_DISPLAY, BY_PAIR), "בן שטרית רפאל")

    def test_middle_name_fallback(self):
        # Transcripts spell her "מיכל מרים וולדיגר"; the key is וולדיגר מיכל.
        self.assertEqual(mh.resolve("מיכל מרים וולדיגר", BY_DISPLAY, BY_PAIR), "וולדיגר מיכל")

    def test_ambiguous_pair_never_guesses(self):
        # Two keys sharing (surname, first given name): the fallback pair
        # is ambiguous and must be dropped — exact displays still work,
        # a middle-name form resolves to neither rather than guessing.
        bd, bp = mh.speaker_map({"כהן אלי", "כהן אלי משה"})
        self.assertNotIn(("כהן", "אלי"), bp)
        self.assertIsNone(mh.resolve("אלי רון כהן", bd, bp))
        self.assertEqual(mh.resolve("אלי כהן", bd, bp), "כהן אלי")

    def test_double_space_spelling_collapses(self):
        # Tenure ships 'גואטה  ששון ששי' (double space), contacts the
        # single-space form — one person, one key, however spelled, and
        # the speaker map winner must not depend on set iteration order.
        self.assertEqual(mh.norm_key("גואטה  ששון ששי"), "גואטה ששון ששי")
        bd, bp = mh.speaker_map({"גואטה ששון ששי", "גואטה  ששון ששי"})
        self.assertEqual(bd, {"ששון ששי גואטה": "גואטה ששון ששי"})
        self.assertEqual(mh.resolve("ששון ששי גואטה", bd, bp), "גואטה ששון ששי")

    def test_name_canon_alias_collapses(self):
        # Contacts spell him גנץ בני, tenure/votes גנץ בנימין (NAME_CANON).
        self.assertEqual(mh.norm_key("גנץ בני"), "גנץ בנימין")
        bd, _ = mh.speaker_map({"גנץ בני", "גנץ בנימין"})
        self.assertEqual(list(bd.values()), ["גנץ בנימין"])

    def test_mk_universe_has_no_spelling_duplicates(self):
        universe = mh.mk_universe()
        self.assertIn("גואטה ששון ששי", universe)
        self.assertNotIn("גואטה  ששון ששי", universe)
        self.assertEqual(len(universe), len({mh.norm_key(n) for n in universe}))

    def test_ministerial_title_stripped(self):
        # Sitting 2246403's ministers were dropped from its plenum_hl
        # shard entirely because the header carries the portfolio.
        bd, bp = mh.speaker_map({"גמליאל גילה", "בן גביר איתמר", "כהן אלמוג",
                                 "סטרוק אורית מלכה", "פינק יאיא (יאיר)",
                                 "כרכבי סבאח מהא"})
        self.assertEqual(mh.resolve("שרת החדשנות, המדע והטכנולוגיה גילה גמליאל", bd, bp),
                         "גמליאל גילה")
        self.assertEqual(mh.resolve("השר לביטחון לאומי איתמר בן גביר", bd, bp), "בן גביר איתמר")
        self.assertEqual(mh.resolve("סגן שר במשרד ראש הממשלה אלמוג כהן", bd, bp), "כהן אלמוג")
        self.assertEqual(mh.resolve("שרת ההתיישבות והמשימות הלאומיות אורית מלכה סטרוק", bd, bp),
                         "סטרוק אורית מלכה")

    def test_parenthesised_nickname(self):
        bd, bp = mh.speaker_map({"פינק יאיא (יאיר)"})
        self.assertEqual(mh.resolve("יאיא (יאיר) פינק", bd, bp), "פינק יאיא (יאיר)")
        self.assertEqual(mh.resolve("יאיא פינק", bd, bp), "פינק יאיא (יאיר)")

    def test_two_token_surname_token_set(self):
        # כרכבי סבאח is one surname; the naive flip reads סבאח מהא כרכבי.
        bd, bp = mh.speaker_map({"כרכבי סבאח מהא"})
        self.assertEqual(mh.resolve("מהא כרכבי סבאח", bd, bp), "כרכבי סבאח מהא")

    def test_official_with_title_still_none(self):
        bd, bp = mh.speaker_map({"גמליאל גילה"})
        self.assertIsNone(mh.resolve("מזכיר הכנסת דן מרזוק", bd, bp))

    def test_guest_resolves_to_none(self):
        self.assertIsNone(mh.resolve("יוסי שריד-לוי", BY_DISPLAY, BY_PAIR))
        self.assertIsNone(mh.resolve("", BY_DISPLAY, BY_PAIR))


if __name__ == "__main__":
    unittest.main()
