// Committee protocol (פרוטוקול ועדה) reader, parsed on demand.
//
// Why a function: same reason as plenum.mjs — fs.knesset.gov.il sends no
// CORS headers, so the browser cannot fetch the .doc (really docx)
// transcripts itself. Committee protocols are only pre-parsed statically
// (data/protocols/*.json, build_protocols.py) for the recent window in
// committees.json (a few hundred sessions); the Knesset held ~10,800
// committee sessions in K25 alone, and pre-parsing all of them would add
// tens of MB of JSON to the repo for files almost nobody opens. Instead:
// GET /.netlify/functions/committee-protocol?id=<CommitteeSessionID>
// resolves the session's פרוטוקול ועדה document (GroupTypeID 23) from the
// official OData service, downloads and parses it here, and returns
// speaker turns in the same shape as data/protocols/*.json. The
// transcripts are immutable, so responses are CDN-cacheable forever.
//
// Security: the client supplies ONLY a numeric session id (CWE-20:
// validated). Document URLs are resolved server-side from OData and
// checked against an fs.knesset.gov.il allowlist before fetching
// (CWE-918: no client-controlled URL is ever fetched). Downloads are
// size-capped (CWE-400). Zip parsing uses node:zlib only — no deps.

import { inflateRawSync } from "node:zlib";

const ODATA = "https://knesset.gov.il/OdataV4/ParliamentInfo/";
const MAX_DOC_BYTES = 8 * 1024 * 1024;
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

// Same turn markers and roles as build_protocols.py (plus דובר_המשך,
// which the static parser's marker set would otherwise swallow).
const ROLE = { "יור": "chair", "דובר": "speaker", "דובר_המשך": "speaker", "אורח": "guest", "קריאה": "interjection", "נושא": "topic", "סיום": "end" };
const MARK = /<<\s*(יור|דובר|אורח|קריאה|נושא|סיום|דובר_המשך)\s*>>\s*([\s\S]*?)<<\s*(?:יור|דובר|אורח|קריאה|נושא|סיום|דובר_המשך)\s*>>/g;

function parseTranscript(text) {
  const turns = [], speakers = {};
  const matches = [...text.matchAll(MARK)];
  for (let i = 0; i < matches.length; i++) {
    const m = matches[i];
    const role = ROLE[m[1]];
    let header = m[2].replace(/\s+/g, " ").trim().replace(/:$/, "").trim();
    const body = text.slice(m.index + m[0].length, i + 1 < matches.length ? matches[i + 1].index : undefined).replace(/\s+/g, " ").trim();
    if (role === "topic") { turns.push({ role: "topic", speaker: "", text: header || body }); continue; }
    if (role === "end") continue;
    let faction = "";
    const fm = header.match(/^(.*?)\s*\(([^)]*)\)\s*$/);
    if (fm) { header = fm[1].trim(); faction = fm[2].trim(); }
    const speaker = header.replace(/^היו["״]ר\s+/, "").trim();
    if (!speaker || !body) continue;
    turns.push({ role, speaker, faction_hint: faction, text: body });
    const s = speakers[speaker] ??= { turns: 0, words: 0, role, faction_hint: faction };
    s.turns += 1;
    s.words += body.split(" ").length;
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
    const docs = await odata(`KNS_DocumentCommitteeSession?$filter=CommitteeSessionID%20eq%20${id}%20and%20GroupTypeID%20eq%2023&$orderby=Id&$select=FilePath`);
    const urls = [];
    for (const d of docs.value || []) {
      const u = new URL(d.FilePath);
      if (u.protocol !== "https:" || u.hostname !== "fs.knesset.gov.il" || !/^\/\d+\/Committees\/[\w.\-]+$/.test(u.pathname)) throw new Error("unexpected doc host");
      urls.push(u.toString());
    }
    if (!urls.length) return Response.json({ error: "no protocol for session" }, { status: 404 });
    const turns = [], speakers = {};
    for (const url of urls) { // a session may have several protocol parts — merge in order
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
    if (!turns.length) return Response.json({ error: "protocol has no parsed turns" }, { status: 404 });
    const body = JSON.stringify({ id, turns, speakers });
    cache.set(id, body);
    return new Response(body, { headers: { "Content-Type": "application/json", "Cache-Control": "public, max-age=86400, s-maxage=604800, immutable" } });
  } catch (e) {
    return Response.json({ error: String(e.message || e) }, { status: 502 });
  }
}
