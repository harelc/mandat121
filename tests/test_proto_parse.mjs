// Paragraph/continuation semantics of the on-demand protocol parsers
// (committee-protocol.mjs + plenum.mjs share parseTranscript). Mirrors
// tests/test_protocols_parse.py for the Python parser: paragraphs
// inside a speech must survive, and << דובר_המשך >> continuations
// merge into the previous turn instead of becoming repeated turns.
// Run: node tests/test_proto_parse.mjs (wired into check.yml)
import assert from "node:assert";

const TEXT = [
  "<< נושא >> סדר היום של הישיבה << נושא >>",
  "<< דובר >> דנה כהן (סיעת הדוגמה): << דובר >>",
  "פסקה ראשונה של הנאום.",
  "פסקה שנייה של אותו נאום.",
  "<< דובר_המשך >> משפט המשך בודד באמצע הנאום.",
  "<< דובר_המשך >> דנה כהן: << דובר_המשך >>",
  "פסקת המשך של אותה דוברת.",
  "<< קריאה >> אבי לוי: << קריאה >>",
  "קריאת ביניים קצרה.",
  "<< הפסקה >> (הישיבה נפסקה בשעה 09:40 ונתחדשה בשעה 10:01.) << הפסקה >>",
  '<< מנהל >> (היו"ר משה גפני, 10:02) << מנהל >>',
  "דברי היושב-ראש כמנהל הדיון.",
  "הישיבה ננעלה בשעה 14:23. << סיום >>",
].join("\n");

let passed = 0;
for (const f of ["committee-protocol", "plenum"]) {
  const { parseTranscript } = await import(`../netlify/functions/${f}.mjs`);
  const { turns, speakers } = parseTranscript(TEXT);
  const speech = turns.filter((t) => t.role !== "topic");
  assert.equal(speech.length, 3, `${f}: speech + interjection + chair`); passed++;
  assert.equal(
    speech[0].text,
    "פסקה ראשונה של הנאום.\n\nפסקה שנייה של אותו נאום.\n\nמשפט המשך בודד באמצע הנאום.\n\nפסקת המשך של אותה דוברת.",
    `${f}: paragraphs preserved, continuation merged`,
  ); passed++;
  assert.ok(turns.every((t) => !t.text.includes("<<")), `${f}: no marker leaks`); passed++;
  assert.equal(speakers["דנה כהן"].turns, 1, `${f}: continuation is not a turn`); passed++;
  assert.equal(speakers["דנה כהן"].words, speech[0].text.split(/\s+/).length, `${f}: words counted once`); passed++;
  assert.ok(turns.some((t) => t.role === "topic" && t.text.includes("נפסקה בשעה")), `${f}: recess becomes a topic`); passed++;
  const chairs = turns.filter((t) => t.role === "chair");
  assert.equal(chairs.length, 1, `${f}: manager marker is a chair turn`); passed++;
  assert.equal(chairs[0].speaker, "משה גפני", `${f}: manager header unwrapped`); passed++;
  assert.ok(chairs[0].text.endsWith("הישיבה ננעלה בשעה 14:23."), `${f}: lone trailing end-marker stripped`); passed++;
}
// A continuation naming a DIFFERENT speaker than the previous turn is
// that speaker's new turn — e.g. the chair's "משפט לסיום" interjection
// between an MK and their resumed closing sentence (sitting 2245727
// rendered Kreutzer's words as Ohana's). Same-speaker continuations
// still merge (case above).
const TEXT2 = [
  "<< דובר >> יצחק קרויזר (סיעת הדוגמה): << דובר >>",
  "גוף הנאום של קרויזר.",
  '<< יור >> היו"ר אמיר אוחנה: << יור >>',
  "חבר הכנסת קרויזר, משפט לסיום.",
  "<< דובר_המשך >> יצחק קרויזר (סיעת הדוגמה): << דובר_המשך >>",
  "אז משפט לסיום. תודה רבה.",
  '<< יור >> היו"ר אמיר אוחנה: << יור >>',
  "תודה רבה.",
].join("\n");
for (const f of ["committee-protocol", "plenum"]) {
  const { parseTranscript } = await import(`../netlify/functions/${f}.mjs`);
  const { turns, speakers } = parseTranscript(TEXT2);
  assert.equal(turns.length, 4, `${f}: resumed speech is its own turn`); passed++;
  assert.equal(turns[1].text, "חבר הכנסת קרויזר, משפט לסיום.", `${f}: chair turn stays the interjection only`); passed++;
  assert.equal(turns[2].speaker, "יצחק קרויזר", `${f}: continuation attributed to its named speaker`); passed++;
  assert.equal(turns[2].role, "speaker", `${f}: resumed turn is a speaker turn`); passed++;
  assert.equal(speakers["יצחק קרויזר"].turns, 2, `${f}: speaker counted for both turns`); passed++;
  assert.equal(speakers["אמיר אוחנה"].turns, 2, `${f}: chair counted for both of his turns`); passed++;
}
console.log(`protocol parse: ${passed} tests passed`);
