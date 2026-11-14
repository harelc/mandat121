// Plenum protocol (דברי הכנסת) reader, parsed on demand.
//
// Why a function: fs.knesset.gov.il sends no CORS headers, so the
// browser cannot fetch the .doc (really docx) transcripts itself, and
// pre-parsing all 418 Knesset-25 sittings would add 150MB+ of JSON to
// the repo. Instead: GET /.netlify/functions/plenum?id=<PlenumSessionID>
// resolves the session's דברי הכנסת documents from the official OData
// service, downloads and parses them here, and returns speaker turns in
// the same shape as data/protocols/*.json (committee protocols). The
// transcripts are immutable, so responses are CDN-cacheable forever.
//
// Security: the client supplies ONLY a numeric session id (CWE-20:
// validated). Document URLs are resolved server-side from OData and
// checked against an fs.knesset.gov.il allowlist before fetching
// (CWE-918: no client-controlled URL is ever fetched). Downloads are
// size-capped (CWE-400). Zip parsing uses node:zlib only — no deps.

import { inflateRawSync } from "node:zlib";

const ODATA = "https://knesset.gov.il/OdataV4/ParliamentInfo/";
const MAX_DOC_BYTES = 4 * 1024 * 1024;
const UA = { "User-Agent": "Mozilla/5.0 mandat121-functions", Accept: "application/json" };
const cache = new Map(); // session id -> response body (warm instances)

function unzipEntry(buf, wanted) {
  let eocd = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 22 - 65536); i--) {
    if (buf.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("not a zip");
  let off = buf.readUInt32LE(eocd + 16);
  const count = buf.readUInt16LE(eocd + 10);
  for (let n = 0; n < count; n++) {
    if (buf.readUInt32LE(off) !== 0x02014b50) break;
    const method = buf.readUInt16LE(off + 10);
    const compSize = buf.readUInt32LE(off + 20);
    const nameLen = buf.readUInt16LE(off + 28);
    const extraLen = buf.readUInt16LE(off + 30);
    const commentLen = buf.readUInt16LE(off + 32);
    const localOff = buf.readUInt32LE(off + 42);
    const name = buf.toString("utf8", off + 46, off + 46 + nameLen);
    if (name === wanted) {
      const lhName = buf.readUInt16LE(localOff + 26);
      const lhExtra = buf.readUInt16LE(localOff + 28);
      const start = localOff + 30 + lhName + lhExtra;
      const comp = buf.subarray(start, start + compSize);
      if (method === 0) return comp.toString("utf8");
      if (method === 8) return inflateRawSync(comp).toString("utf8");
      throw new Error(`zip method ${method}`);
    }
    off += 46 + nameLen + extraLen + commentLen;
  }
  throw new Error(`entry ${wanted} not found`);
}

function docxText(xml) {
  return xml.replace(/<\/w:p>/g, "\n").replace(/<[^>]+>/g, "")
    .replace(/&quot;/g, '"').replace(/&apos;/g, "'")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

const ROLE = { "יור": "chair", "יו\"ר": "chair", "דובר": "speaker", "דובר_המשך": "cont", "אורח": "guest", "קריאה": "interjection", "נושא": "topic", "סיום": "end", "הפסקה": "topic", "מנהל": "chair" };
// Turn text keeps its paragraphs: docx paragraphs arrive as lines
// (docxText maps </w:p> to \n) and are stored joined by a blank line,
// rendered as separate <p>s. Collapsing all whitespace — the pre-fix
// behaviour — fused whole speeches into one wall of text.
const parasOf = (body) => body.split("\n").map((p) => p.replace(/\s+/g, " ").trim()).filter(Boolean).join("\n\n");
const MARK = /<<\s*(יו"ר|יור|דובר|אורח|קריאה|נושא|סיום|דובר_המשך|הפסקה|מנהל)\s*>>\s*([\s\S]*?)<<\s*(?:יו"ר|יור|דובר|אורח|קריאה|נושא|סיום|דובר_המשך|הפסקה|מנהל)\s*>>/g;

const MARK1 = /<<\s*(יו"ר|יור|דובר|אורח|קריאה|נושא|סיום|דובר_המשך|הפסקה|מנהל)\s*>>/g;
/* Stenographers sometimes drop a LONE marker mid-speech (seen in sitting
   2227967: "<< דובר_המשך >>אבל עד שנשנה את החוק…" with no name and no
   closing twin). The paired MARK regex then treats that lone marker as a
   header opening and the next turn's opening as its close — every speaker
   after it shifts by one turn (body becomes "speaker", next name becomes
   "text"). Drop lone markers (keeping the text after them as body) before
   pairing: a real header pair encloses a short text ending in ":" or ")"
   (topics/recess/end markers: any short text). */
function stripLoneMarkers(text) {
  const occ = [...text.matchAll(MARK1)];
  if (occ.length < 2) return text;
  const lone = new Set();
  for (let i = 0; i < occ.length; i++) {
    if (i + 1 >= occ.length) { lone.add(i); continue; }
    const between = text.slice(occ[i].index + occ[i][0].length, occ[i + 1].index).replace(/\s+/g, " ").trim();
    const paired = between.length > 0 && between.length <= 600 && (between.endsWith(":") || between.endsWith(")") || ["נושא", "הפסקה", "סיום"].includes(occ[i][1]));
    if (paired) i++; else lone.add(i);
  }
  if (!lone.size) return text;
  let out = "", last = 0;
  occ.forEach((m, i) => { if (lone.has(i)) { out += text.slice(last, m.index); last = m.index + m[0].length; } });
  return out + text.slice(last);
}

export function parseTranscript(text) {
  text = stripLoneMarkers(text);
  const turns = [], speakers = {};
  const matches = [...text.matchAll(MARK)];
  for (let i = 0; i < matches.length; i++) {
    const m = matches[i];
    let role = ROLE[m[1]];
    let header = m[2].replace(/\s+/g, " ").trim().replace(/:$/, "").trim();
    const body = parasOf(text.slice(m.index + m[0].length, i + 1 < matches.length ? matches[i + 1].index : undefined).replace(/\s*<<\s*סיום\s*>>\s*$/, "")); // a lone trailing end-marker has no pair to match
    if (role === "topic") { turns.push({ role: "topic", speaker: "", text: header || body }); continue; }
    if (role === "end") continue;
    if (role === "cont") {
      // << דובר_המשך >>: the same speaker continues — another paragraph
      // of the previous turn, not a new turn of their own.
      const prev = turns[turns.length - 1];
      if (prev && ["speaker", "chair", "guest"].includes(prev.role) && body) {
        prev.text += "\n\n" + body;
        if (speakers[prev.speaker]) speakers[prev.speaker].words += body.split(/\s+/).length;
        continue;
      }
      role = "speaker"; // a continuation with nothing before it
    }
    let faction = "";
    if (header.startsWith("(") && header.endsWith(")")) {
      header = header.slice(1, -1).replace(/,\s*\d{1,2}:\d{2}\s*$/, "").trim(); // << מנהל >> wraps the whole name: (היו"ר שם, 14:08)
    } else {
      const fm = header.match(/^(.*?)\s*\(([^)]*)\)\s*$/);
      if (fm) { header = fm[1].trim(); faction = fm[2].trim(); }
    }
    const speaker = header.replace(/^היו["״]ר\s+/, "").trim();
    if (!speaker || !body) continue;
    turns.push({ role, speaker, faction_hint: faction, text: body });
    const s = speakers[speaker] ??= { turns: 0, words: 0, role, faction_hint: faction };
    s.turns += 1;
    s.words += body.split(/\s+/).length;
  }
  return { turns, speakers };
}

async function odata(path) {
  const r = await fetch(ODATA + path, { headers: UA });
  if (!r.ok) throw new Error(`odata ${r.status}`);
  return r.json();
}

export default async function handler(request) {
  if (request.method !== "GET") return new Response("method not allowed", { status: 405 });
  const id = new URL(request.url).searchParams.get("id") || "";
  if (!/^\d{1,10}$/.test(id)) return Response.json({ error: "bad id" }, { status: 400 });
  if (cache.has(id)) {
    return new Response(cache.get(id), { headers: { "Content-Type": "application/json", "Cache-Control": "public, max-age=86400, s-maxage=604800, immutable" } });
  }
  try {
    const docs = await odata(`KNS_DocumentPlenumSession?$filter=PlenumSessionID%20eq%20${id}%20and%20GroupTypeID%20eq%2028&$orderby=Id&$select=FilePath`);
    const urls = [];
    for (const d of docs.value || []) {
      const u = new URL(d.FilePath);
      if (u.protocol !== "https:" || u.hostname !== "fs.knesset.gov.il" || !/^\/\d+\/Plenum\/[\w.\-]+$/.test(u.pathname)) throw new Error("unexpected doc host");
      urls.push(u.toString());
    }
    if (!urls.length) return Response.json({ error: "no transcript for session" }, { status: 404 });
    const turns = [], speakers = {};
    for (const url of urls) { // a sitting may have several parts — merge in order
      const r = await fetch(url, { headers: { "User-Agent": UA["User-Agent"] } });
      if (!r.ok) throw new Error(`doc ${r.status}`);
      const buf = Buffer.from(await r.arrayBuffer());
      if (buf.length > MAX_DOC_BYTES) throw new Error("doc too large");
      const parsed = parseTranscript(docxText(unzipEntry(buf, "word/document.xml")));
      turns.push(...parsed.turns);
      for (const [name, s] of Object.entries(parsed.speakers)) {
        const t = speakers[name] ??= { turns: 0, words: 0, role: s.role, faction_hint: s.faction_hint };
        t.turns += s.turns; t.words += s.words;
      }
    }
    const body = JSON.stringify({ id, turns, speakers });
    cache.set(id, body);
    return new Response(body, { headers: { "Content-Type": "application/json", "Cache-Control": "public, max-age=86400, s-maxage=604800, immutable" } });
  } catch (e) {
    return Response.json({ error: String(e.message || e) }, { status: 502 });
  }
}
