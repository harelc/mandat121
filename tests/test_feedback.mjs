// Validation tests for netlify/functions/feedback.mjs (pure validator only;
// the Blobs-backed handler is exercised against the deployed function).
// Run: node tests/test_feedback.mjs
import assert from "node:assert";
import { validateSubmission, LIMITS } from "../netlify/functions/feedback.mjs";

let passed = 0;
const test = (name, fn) => { fn(); passed++; console.log(`ok ${passed} - ${name}`); };

test("accepts a minimal bug report", () => {
  const v = validateSubmission({ type: "bug", text: "הכפתור לא עובד" });
  assert.equal(v.ok, true);
  assert.equal(v.value.consent, false);
  assert.equal(v.value.name, "");
});

test("rejects unknown type and too-short text", () => {
  assert.equal(validateSubmission({ type: "spam", text: "hello there" }).ok, false);
  assert.equal(validateSubmission({ type: "idea", text: "ab" }).ok, false);
});

test("honeypot is flagged for silent drop", () => {
  const v = validateSubmission({ type: "idea", text: "רעיון טוב", hp: "x" });
  assert.equal(v.ok, false);
  assert.equal(v.error, "honeypot");
});

test("caps text and name, strips control chars", () => {
  const v = validateSubmission({ type: "testimonial", text: "א".repeat(5000) + "", name: "x".repeat(500), consent: true });
  assert.equal(v.ok, true);
  assert.equal(v.value.text.length, LIMITS.text);
  assert.equal(v.value.name.length, LIMITS.name);
  assert.equal(v.value.consent, true);
});

console.log(`${passed} passed`);
