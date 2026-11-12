#!/usr/bin/env python3
"""Offline tests for build_cmt_hl helpers (marker cleaning).

Run: python3 -m unittest discover -s tests
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


ch = load_module("build_cmt_hl.py")


class ExcerptTest(unittest.TestCase):
    def test_continuation_marker_and_name_stripped(self):
        out = ch.excerpt_of("דברים חשובים << דובר_המשך >> גור בליי: המשך הדברים")
        self.assertNotIn("<<", out)
        self.assertNotIn("בליי", out)
        self.assertIn("המשך הדברים", out)

    def test_inline_marker_stripped_text_kept(self):
        out = ch.excerpt_of("אני אומר << הצח >> את הדברים הבאים")
        self.assertNotIn("<<", out)
        self.assertIn("את הדברים הבאים", out)

    def test_truncates_to_300(self):
        self.assertLessEqual(len(ch.excerpt_of("מילה " * 200)), 300)


if __name__ == "__main__":
    unittest.main()
