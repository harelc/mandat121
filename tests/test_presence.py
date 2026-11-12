#!/usr/bin/env python3
"""Offline tests for build_presence.py (K26 lobby polling).

Arrival order is a polling artefact: the lobby API reports only
IsPresent, so these pin the merge semantics the UI labels rely on —
first poll that sees an MK wins, re-entry keeps the first time, only
Sun–Thu (Asia/Jerusalem) polls record, and by_mk is a pure derivation
of the day lists. Run: python3 -m unittest discover -s tests
"""
import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent


def load_module(fname):
    spec = importlib.util.spec_from_file_location(fname[:-3], ROOT / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bp = load_module("build_presence.py")


def mk(mk_id, last, first, present=True):
    return {"MkId": mk_id, "Lastname": last, "Firstname": first, "IsPresent": present}


def at(iso):
    return datetime.fromisoformat(iso)


class MergePollTest(unittest.TestCase):
    # 2026-11-12 is a Thursday; 2026-11-13 Friday, 2026-11-15 Sunday.
    def test_first_poll_marks_at_first_poll_and_orders_by_first_seen(self):
        state = bp.empty_state()
        state, new = bp.merge_poll(state, [mk(1, "כהן", "אלי", True),
                                           mk(2, "לוי", "דנה", False)],
                                   at("2026-11-12T07:30:00+02:00"))
        self.assertEqual(new, ["כהן אלי"])
        state, new = bp.merge_poll(state, [mk(2, "לוי", "דנה", True),
                                           mk(1, "כהן", "אלי", True)],
                                   at("2026-11-12T08:05:00+02:00"))
        self.assertEqual(new, ["לוי דנה"])
        arrivals = state["days"]["2026-11-12"]["arrivals"]
        self.assertEqual([a["name"] for a in arrivals], ["כהן אלי", "לוי דנה"])
        self.assertTrue(arrivals[0]["at_first_poll"])
        self.assertFalse(arrivals[1]["at_first_poll"])

    def test_leaving_and_returning_keeps_first_seen(self):
        state = bp.empty_state()
        poll = lambda iso, present: bp.merge_poll(
            state, [mk(1, "כהן", "אלי", present)], at(iso))
        poll("2026-11-12T08:00:00+02:00", True)
        poll("2026-11-12T09:00:00+02:00", False)
        _, new = poll("2026-11-12T10:00:00+02:00", True)
        self.assertEqual(new, [])
        a = state["days"]["2026-11-12"]["arrivals"][0]
        self.assertTrue(a["first_seen"].startswith("2026-11-12T08:00"))

    def test_friday_and_saturday_record_nothing_unless_forced(self):
        state = bp.empty_state()
        for iso in ("2026-11-13T08:00:00+02:00", "2026-11-14T08:00:00+02:00"):
            state, new = bp.merge_poll(state, [mk(1, "כהן", "אלי")], at(iso))
            self.assertEqual(new, [])
        self.assertEqual(state["days"], {})
        state, new = bp.merge_poll(state, [mk(1, "כהן", "אלי")],
                                   at("2026-11-13T08:00:00+02:00"), force=True)
        self.assertEqual(new, ["כהן אלי"])

    def test_utc_instant_is_bucketed_by_jerusalem_day(self):
        # 22:30 UTC in winter = 00:30 next day in Jerusalem (a Friday) —
        # must not be recorded on Thursday's list.
        state = bp.empty_state()
        state, new = bp.merge_poll(state, [mk(1, "כהן", "אלי")],
                                   datetime(2026, 11, 12, 22, 30, tzinfo=timezone.utc))
        self.assertEqual(new, [])
        self.assertEqual(state["days"], {})

    def test_by_mk_counts_workdays_across_days(self):
        state = bp.empty_state()
        bp.merge_poll(state, [mk(1, "כהן", "אלי")], at("2026-11-12T08:00:00+02:00"))
        bp.merge_poll(state, [mk(1, "כהן", "אלי"), mk(2, "לוי", "דנה")],
                      at("2026-11-15T08:00:00+02:00"))  # Sunday
        self.assertEqual(state["by_mk"]["כהן אלי"]["days"], 2)
        self.assertEqual(state["by_mk"]["כהן אלי"]["dates"],
                         ["2026-11-12", "2026-11-15"])
        self.assertEqual(state["by_mk"]["לוי דנה"]["days"], 1)
        self.assertEqual(state["_meta"]["workdays_tracked"], 2)
        self.assertEqual(state["_meta"]["tracking_since"], "2026-11-12")

    def test_empty_day_opens_without_arrivals(self):
        state = bp.empty_state()
        state, new = bp.merge_poll(state, [mk(1, "כהן", "אלי", False)],
                                   at("2026-11-12T07:00:00+02:00"))
        self.assertEqual(new, [])
        self.assertEqual(state["days"]["2026-11-12"]["arrivals"], [])
        self.assertEqual(state["_meta"]["workdays_tracked"], 1)


if __name__ == "__main__":
    unittest.main()
