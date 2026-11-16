#!/usr/bin/env python3
"""Offline tests for build_plenum_hl.session_hl.

Pins the residue contract build_mk_highlights.py consumes: only
MK-resolved speakers are stored (guests/officials dropped), only
substantive turns become candidates, at most PER_SPEAKER per speaker,
and scores order them. Run: python3 -m unittest discover -s tests
"""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent


def load_module(fname):
    spec = importlib.util.spec_from_file_location(fname[:-3], ROOT / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ph = load_module("build_plenum_hl.py")
TEXT = " ".join(f"word{i}" for i in range(45))  # 45 distinct content terms


def turn(speaker, text=TEXT, role="speaker"):
    return {"role": role, "speaker": speaker, "faction_hint": "", "text": text}


class SessionHlTest(unittest.TestCase):
    def doc(self):
        return {"id": 123, "date": "2024-01-01",
                "speakers": {"שמחה רוטמן": {"turns": 3, "words": 135, "role": "speaker", "faction_hint": ""},
                             "אורח כלשהו": {"turns": 1, "words": 45, "role": "guest", "faction_hint": ""}},
                "turns": [turn("שמחה רוטמן"), turn("שמחה רוטמן"), turn("שמחה רוטמן"),
                          turn("אורח כלשהו", role="guest"),
                          turn("שמחה רוטמן", text="קצר מדי")]}

    def test_only_mk_speakers_and_substantive_turns_kept(self):
        out = ph.session_hl(self.doc(), {"number": 7, "date": "2024-01-01"}, {}, 418)
        self.assertEqual(list(out["speakers"]), ["שמחה רוטמן"])
        self.assertEqual(list(out["top"]), ["שמחה רוטמן"])
        # 3 substantive turns in, PER_SPEAKER cap keeps the best 2
        self.assertEqual(len(out["top"]["שמחה רוטמן"]), ph.PER_SPEAKER)
        self.assertTrue(all(r["words"] >= ph.MIN_WORDS for r in out["top"]["שמחה רוטמן"]))

    def test_marker_leak_stripped_from_excerpt(self):
        d = self.doc()
        d["turns"][0] = turn("שמחה רוטמן", text="<< הצח >> " + TEXT)
        out = ph.session_hl(d, {"number": 7, "date": "2024-01-01"}, {}, 418)
        self.assertNotIn("<<", out["top"]["שמחה רוטמן"][0]["excerpt"])

    def test_residue_stamped_and_committed_files_current(self):
        # A parser-attribution fix must regenerate the residue in the
        # same change: stale files silently credited continuations to
        # whoever interjected before them (sitting 2245727: an MK's
        # 293-word maiden speech counted as 3 words, the rest went to
        # the chair). This fails until data/plenum_hl/* is regenerated.
        out = ph.session_hl(self.doc(), {"number": 7, "date": "2024-01-01"}, {}, 418)
        self.assertEqual(out["parser_version"], ph.PARSER_VERSION)
        hl_dir = ROOT / "data" / "plenum_hl"
        stale = [f.name for f in hl_dir.glob("*.json")
                 if json.loads(f.read_text("utf-8")).get("parser_version") != ph.PARSER_VERSION]
        self.assertEqual(stale, [], f"regenerate with build_plenum_hl.py: {stale[:5]}…")

    def test_session_df_counts_sittings_per_word(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "1_2.json").write_text(json.dumps({"abc": "10:3,5:2", "de": "10:1"}), "utf-8")
            (p / "manifest.json").write_text("{}", "utf-8")
            df = ph.session_df(p)
        self.assertEqual(df["abc"], 2)
        self.assertEqual(df["de"], 1)


if __name__ == "__main__":
    unittest.main()
