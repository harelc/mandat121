// Speaker -> MK-card resolution in the protocol readers (index.html
// protoSpeakerKey). Protocol speakers are given-first; MK keys are
// surname-first. The old map inverted disp() over the K25 vote/photo
// files only and matched the raw string exactly, so on Knesset-26
// sittings most MKs never linked: K26-only members were absent, and
// returning members failed on ministerial titles, parenthesised
// nicknames, middle names, and two-token surnames.
//
// This extracts the resolver (plus FORWARD/NAME_CANON/disp/normName)
// from index.html, feeds it the real data files as the name universe,
// and checks every speaker of plenum sitting 2246403 (fetched live
// 2026-11-16) — all MKs resolve, the Knesset secretary and the
// קריאה/קריאות crowd markers do not.
// Run: node tests/test_proto_speaker.mjs (wired into check.yml)
import assert from "node:assert";
import fs from "node:fs";

const html = fs.readFileSync(new URL("../index.html", import.meta.url), "utf8");
const grab = (a, b) => {
  const i = html.indexOf(a), j = html.indexOf(b, i);
  assert.ok(i >= 0 && j > i, `extract ${a}`);
  return html.slice(i, j);
};
const code = [
  grab("const FORWARD=", "function realFaction"),
  grab("const dedupeNames=", "\n"),
  grab("let _g2k=", "function protoColor"),
].join("\n");

const J = (f) => JSON.parse(fs.readFileSync(new URL(`../data/${f}`, import.meta.url), "utf8"));
const normKeys = (o, normName) => { const r = {}; for (const [k, v] of Object.entries(o || {})) r[normName(k)] = v; return r };

let passed = 0;
const run = new Function("J", "normKeys", `
  ${code}
  const MKP = normKeys(J("mk_photos.json"), normName);
  const mkIndex = {}; for (const n of J("k25_vote_results.json").names) mkIndex[normName(n)] = {};
  const CONTACTS = normKeys(J("mk_contacts.json").by_name, normName);
  const TENURE = normKeys(J("k25_tenure.json").tenure, normName);
  const CONTACTS26 = normKeys(J("k26_contacts.json").by_name, normName);
  const K26_TENURE = normKeys(J("k26_tenure.json").tenure, normName);
  const IDX26 = {};
  const K26 = J("k26_preview.json");
  for (const p of K26.parties || []) { p.elected = (p.elected || []).map(normName); p.candidates = (p.candidates || []).map(normName); }
  return { protoSpeakerKey, disp, normName };
`);
const { protoSpeakerKey } = run(J, normKeys);
const eq = (name, key) => { assert.equal(protoSpeakerKey(name), key, `${name} -> ${key}`); passed++ };
const no = (name) => { assert.equal(protoSpeakerKey(name), null, `${name} must not link`); passed++ };

// Every MK speaker of sitting 2246403, in the transcript's spelling.
for (const n of [
  "אמיר אוחנה", "אלי גולדשמידט", "צבי מור", "גדי איזנקוט", "איתן גינזבורג",
  "נעמה לזימי", "מאיר פרוש", "טליה לנקרי", "יואב בן צור", "טלי גוטליב",
  "יפה טבג'ה", "מהא כרכבי סבאח", "אלקס ריף", "לימור סון הר מלך", "אליסף פרץ",
  "שירה שפירא", "יאיא (יאיר) פינק", "מיכל רוזין", "גבי לסקי", "ואליד אלהואשלה",
  "חסן אלנסאסרה", "תאיר איפרגן", "פאתן גטאס", "יצחק קרויזר", "יוסף עטאונה",
  "אלמוג כהן", "רועי פולקמן", "אחמד דראושה", "רפאל בן שטרית", "אופיר כץ",
  "שאול מרידור",
]) { assert.ok(protoSpeakerKey(n), `${n} (K26 sitting) links`); passed++ }

// The tricky spellings, with their exact canonical keys.
eq("שרת החדשנות, המדע והטכנולוגיה גילה גמליאל", "גמליאל גילה"); // title + commas
eq("השר לביטחון לאומי איתמר בן גביר", "בן גביר איתמר"); // title, two-token surname
eq("סגן שר במשרד ראש הממשלה אלמוג כהן", "כהן אלמוג"); // deputy-minister title
eq("שרת ההתיישבות והמשימות הלאומיות אורית מלכה סטרוק", "סטרוק אורית מלכה"); // title + middle name in the key
eq("יאיא (יאיר) פינק", "פינק יאיא (יאיר)"); // parenthesised nickname
eq("מהא כרכבי סבאח", "כרכבי סבאח מהא"); // two-token surname disp() flips naively
eq("רפאל בן שטרית", "בן שטרית רפאל"); // בן surname
eq("נעמה מרים לזימי", "לזימי נעמה"); // middle name only in the transcript

// K25 regression: plain given-first speakers of the old era still link.
eq("שמחה רוטמן", "רוטמן שמחה");
eq("אורית מלכה סטרוק", "סטרוק אורית מלכה");

// Not MKs — must stay plain text.
no("מזכיר הכנסת דן מרזוק"); // Knesset secretary, even after title-stripping
no("קריאה");
no("קריאות");
no("אברהם אבינו");

console.log(`protocol speaker resolution: ${passed} tests passed`);
