// Merge-semantics tests for netlify/functions/presence-poll.mjs — the
// JS port of build_presence.py (retired with the GH polling workflow).
// Run: node tests/test_presence_merge.mjs  (no deps; wired into check.yml)
import assert from "node:assert";
import { emptyState, mergePoll } from "../netlify/functions/presence-poll.mjs";

const mk = (last, first, id, present = true) => ({ Lastname: last, Firstname: first, MkId: id, IsPresent: present });
let passed = 0;
const test = (name, fn) => { fn(); passed++; console.log(`ok ${passed} - ${name}`) };

test("first poll of a workday opens the day; everyone present is at_first_poll", () => {
  const s = emptyState();
  const { added } = mergePoll(s, [mk("כהן", "דנה", 1), mk("לוי", "אבי", 2, false)], new Date("2026-11-12T05:30:00Z")); // Thu 07:30 IST
  assert.deepEqual(added, ["כהן דנה"]);
  const day = s.days["2026-11-12"];
  assert.equal(day.arrivals.length, 1);
  assert.equal(day.arrivals[0].at_first_poll, true);
  assert.equal(day.arrivals[0].first_seen, "2026-11-12T07:30:00+02:00");
  assert.equal(s.by_mk["כהן דנה"].days, 1);
  assert.equal(s._meta.tracking_since, "2026-11-12");
});

test("later arrivals append in poll order; leave-and-return keeps first_seen", () => {
  const s = emptyState();
  mergePoll(s, [mk("כהן", "דנה", 1)], new Date("2026-11-12T05:30:00Z"));
  const r2 = mergePoll(s, [mk("לוי", "אבי", 2)], new Date("2026-11-12T06:05:00Z")); // כהן left; לוי arrives
  assert.deepEqual(r2.added, ["לוי אבי"]);
  const r3 = mergePoll(s, [mk("כהן", "דנה", 1)], new Date("2026-11-12T07:00:00Z")); // כהן returns
  assert.deepEqual(r3.added, []);
  const day = s.days["2026-11-12"];
  assert.deepEqual(day.arrivals.map((a) => a.name), ["כהן דנה", "לוי אבי"]);
  assert.equal(day.arrivals[0].first_seen, "2026-11-12T07:30:00+02:00");
  assert.equal(day.arrivals[1].at_first_poll, false);
});

test("Friday and Saturday polls merge nothing (Sun–Thu gate)", () => {
  const s = emptyState();
  assert.deepEqual(mergePoll(s, [mk("כהן", "דנה", 1)], new Date("2026-11-13T06:00:00Z")).added, []); // Fri
  assert.deepEqual(mergePoll(s, [mk("כהן", "דנה", 1)], new Date("2026-11-14T06:00:00Z")).added, []); // Sat
  assert.deepEqual(Object.keys(s.days), []);
  const forced = mergePoll(s, [mk("כהן", "דנה", 1)], new Date("2026-11-13T06:00:00Z"), true);
  assert.deepEqual(forced.added, ["כהן דנה"]); // force = manual checks only
});

test("instants bucket by Jerusalem date, not UTC date", () => {
  const s = emptyState();
  mergePoll(s, [mk("כהן", "דנה", 1)], new Date("2026-11-11T22:30:00Z")); // Wed 22:30 UTC = Thu 00:30 IST
  assert.deepEqual(Object.keys(s.days), ["2026-11-12"]);
  assert.equal(s.days["2026-11-12"].arrivals[0].first_seen, "2026-11-12T00:30:00+02:00");
});

test("by_mk accumulates one entry per MK per day across days", () => {
  const s = emptyState();
  mergePoll(s, [mk("כהן", "דנה", 1), mk("לוי", "אבי", 2)], new Date("2026-11-12T05:30:00Z")); // Thu
  mergePoll(s, [mk("כהן", "דנה", 1)], new Date("2026-11-15T05:30:00Z")); // Sun
  assert.equal(s.by_mk["כהן דנה"].days, 2);
  assert.deepEqual(s.by_mk["כהן דנה"].dates, ["2026-11-12", "2026-11-15"]);
  assert.equal(s.by_mk["לוי אבי"].days, 1);
  assert.equal(s._meta.workdays_tracked, 2);
});

test("a workday with nobody present still opens (honest empty day)", () => {
  const s = emptyState();
  const { added } = mergePoll(s, [mk("כהן", "דנה", 1, false)], new Date("2026-11-12T05:30:00Z"));
  assert.deepEqual(added, []);
  assert.deepEqual(s.days["2026-11-12"].arrivals, []);
});

console.log(`presence merge: ${passed} tests passed`);
