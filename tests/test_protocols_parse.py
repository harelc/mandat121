"""parse_text paragraph/continuation semantics (build_protocols.py).

Regression tests for the "huge chunk without paragraph breaks" bug:
docx paragraphs inside one speech were whitespace-collapsed into a
single blob, and << דובר_המשך >> continuations — unknown to the
marker set — leaked (marker included) into the previous turn.
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_protocols", ROOT / "build_protocols.py")
bp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bp)

SESSION = {"id": 1, "committee": "ועדת בדיקה", "start": "2026-01-01T10:00"}

# Real marker layout: each header is wrapped in a doubled marker,
# body follows, paragraphs are lines (docx </w:p> -> \n upstream).
TEXT = (
    "<< נושא >> סדר היום של הישיבה << נושא >>\n"
    "<< דובר >> דנה כהן (סיעת הדוגמה): << דובר >>\n"
    "פסקה ראשונה של הנאום.\n"
    "פסקה שנייה של אותו נאום.\n"
    "<< דובר_המשך >> דנה כהן: << דובר_המשך >>\n"
    "פסקת המשך של אותה דוברת.\n"
    "<< קריאה >> אבי לוי: << קריאה >>\n"
    "קריאת ביניים קצרה.\n"
    "<< דובר >> דנה כהן (סיעת הדוגמה): << דובר >>\n"
    "תשובה אחרי הקריאה.\n"
    "<< הפסקה >> (הישיבה נפסקה בשעה 09:40 ונתחדשה בשעה 10:01.) << הפסקה >>\n"
    '<< מנהל >> (היו"ר משה גפני, 10:02) << מנהל >>\n'
    "דברי היושב-ראש כמנהל הדיון.\n"
    '<< יו"ר >> היו"ר משה גפני: << יו"ר >>\n'
    "עוד דברי יושב-ראש.\n"
    "הישיבה ננעלה בשעה 14:23. << סיום >>\n"
)


class ParseTextTests(unittest.TestCase):
    def setUp(self):
        self.doc = bp.parse_text(SESSION, TEXT)
        self.turns = [t for t in self.doc["turns"] if t["role"] != "topic"]

    def test_paragraphs_survive_inside_a_turn(self):
        first = self.turns[0]
        self.assertEqual(
            first["text"],
            "פסקה ראשונה של הנאום.\n\nפסקה שנייה של אותו נאום.\n\nפסקת המשך של אותה דוברת.",
        )

    def test_continuation_merges_not_leaks(self):
        self.assertEqual(len(self.turns), 5)  # speech, interjection, reply, chair x2
        for t in self.doc["turns"]:
            self.assertNotIn("<<", t["text"])
            self.assertNotIn("דובר_המשך", t["text"])

    def test_continuation_words_count_to_the_speaker_once(self):
        spk = self.doc["speakers"]["דנה כהן"]
        self.assertEqual(spk["turns"], 2)  # merged continuation is not a turn
        self.assertEqual(spk["words"], len(self.turns[0]["text"].split()) + len(self.turns[2]["text"].split()))

    def test_recess_becomes_a_topic_and_manager_a_chair_turn(self):
        topics = [t for t in self.doc["turns"] if t["role"] == "topic"]
        self.assertTrue(any("נפסקה בשעה" in t["text"] for t in topics))
        chairs = [t for t in self.turns if t["role"] == "chair"]
        self.assertEqual(len(chairs), 2)
        self.assertTrue(all(t["speaker"] == "משה גפני" for t in chairs))
        # the lone trailing end-marker is stripped, its sentence kept
        self.assertTrue(self.turns[-1]["text"].endswith("הישיבה ננעלה בשעה 14:23."))
        for t in self.doc["turns"]:
            self.assertNotIn("<<", t["text"])

    def test_continuation_without_previous_turn_becomes_a_turn(self):
        doc = bp.parse_text(SESSION, "<< דובר_המשך >> דנה כהן: << דובר_המשך >>\nפותחת בלי הקדמה.\n<< סיום >> << סיום >>\n")
        speech = [t for t in doc["turns"] if t["role"] == "speaker"]
        self.assertEqual(len(speech), 1)
        self.assertEqual(speech[0]["text"], "פותחת בלי הקדמה.")


if __name__ == "__main__":
    unittest.main()
