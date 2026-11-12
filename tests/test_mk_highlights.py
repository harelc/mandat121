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

    def test_guest_resolves_to_none(self):
        self.assertIsNone(mh.resolve("יוסי שריד-לוי", BY_DISPLAY, BY_PAIR))
        self.assertIsNone(mh.resolve("", BY_DISPLAY, BY_PAIR))


if __name__ == "__main__":
    unittest.main()
