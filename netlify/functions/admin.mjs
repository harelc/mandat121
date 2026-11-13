import { timingSafeEqual } from "node:crypto";
import { getStore } from "@netlify/blobs";

// Secret admin dashboard for the site owner.
//   Page: GET /.netlify/functions/admin          (HTML shell, asks for token)
//   Data: GET /.netlify/functions/admin?data=1   (header x-hit-token)
// The token is the same HIT_READ_TOKEN env var that gates the hit.mjs
// report. Never set / hardcode it here, never log it. Responses are
// no-store, and the data payload contains only aggregates plus the
// truncated hashed uids already stored by stars.mjs — no raw IPs, no
// raw visitor ids (per the privacy design of hit.mjs/stars.mjs).
// Auth note (OWASP A07 / CWE-208): token comparison is constant-time,
// failures return a generic 403 with no hint which part failed.

function authorized(request) {
  const token = process.env.HIT_READ_TOKEN;
  if (!token) return false;
  const given = request.headers.get("x-hit-token") || "";
  const a = Buffer.from(given), b = Buffer.from(token);
  return a.length === b.length && timingSafeEqual(a, b);
}

const PAGE = `<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>אדמין · המנדט ה־121</title>
<style>body{font-family:system-ui,sans-serif;background:#f4efe3;color:#26221a;margin:2rem;line-height:1.6}
.card{background:#fbf8f1;border:1px solid #ddd2ba;border-radius:16px;padding:1rem 1.2rem;margin-bottom:1rem}
table{border-collapse:collapse;width:100%;font-size:.88rem}td,th{border-bottom:1px solid #ddd2ba;padding:.3rem .5rem;text-align:right}
b.n{color:#476b9e;font-size:1.5rem}.muted{color:#857c66;font-size:.85rem}code{background:#f1ebdb;padding:.05rem .35rem;border-radius:6px}</style></head>
<body><h1>🎛️ אדמין — המנדט ה־121</h1><div id="app" class="muted">טוען…</div>
<script>
var tok = sessionStorage.getItem('m121-admin') || prompt('טוקן אדמין (HIT_READ_TOKEN):') || '';
sessionStorage.setItem('m121-admin', tok);
fetch(location.pathname + '?data=1', { headers: { 'x-hit-token': tok }, cache: 'no-store' })
 .then(function(r){ if(!r.ok) throw new Error('forbidden'); return r.json() })
 .then(function(j){
   var N = function(x){ return (x||0).toLocaleString('he-IL') };
   var esc = function(s){ return String(s==null?'':s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c] }) };
   var BASE = 'https://mandat121.netlify.app/';
   var linkFor = function(kind, key){
     if(kind==='mk') return BASE + '#mk=' + encodeURIComponent(key);
     if(kind==='bill') return BASE + '#bill=' + encodeURIComponent(key);
     if(kind==='committee') return BASE + '#committee=' + encodeURIComponent(key);
     if(kind==='faction') return BASE + '#faction=' + encodeURIComponent(key);
     return '';
   };
   var entLink = function(kind, key){ var u = linkFor(kind, key); var label = esc(key); return u ? '<a href="' + u + '" target="_blank" rel="noopener">' + label + ' ↗</a>' : label };
   var h = '';
   h += '<div class="card"><h3>תנועה</h3>'
     + '<div>היום: <b class="n">' + N(j.traffic.today.pageviews) + '</b> צפיות · ' + N(j.traffic.today.unique_ips) + ' מבקרים ייחודיים · בשעה האחרונה ' + N(j.traffic.hour.pageviews) + ' צפיות · <b>עכשיו באתר: ' + N(j.traffic.online) + '</b></div>'
     + '<div class="muted">דפים מובילים היום: ' + j.traffic.top_pages.map(function(p){ return esc(p.path) + ' (' + N(p.pageviews) + ')' }).join(' · ') + '</div></div>';
   h += '<div class="card"><h3>★ הכי נעקבים (' + j.stars.total_entities + ' ישויות)</h3><table><tr><th>ישות</th><th>סוג</th><th>עוקבים</th></tr>'
     + j.stars.top.map(function(s){ return '<tr><td>' + entLink(s.kind, s.key) + '</td><td>' + esc(s.kind) + '</td><td>' + N(s.count) + '</td></tr>' }).join('') + '</table></div>';
   h += '<div class="card"><h3>יומן פעולות ★ (אחרונות ' + j.star_log.length + ')</h3><table><tr><th>זמן</th><th>פעולה</th><th>ישות</th><th>סוג</th><th>סה״כ אחרי</th><th>מבקר</th></tr>'
     + j.star_log.slice().reverse().map(function(e){ return '<tr><td>' + esc(e.t.replace('T',' ').slice(0,19)) + '</td><td>' + (e.action==='follow'?'★ follow':'☆ unfollow') + '</td><td>' + entLink(e.kind, e.key) + '</td><td>' + esc(e.kind) + '</td><td>' + N(e.count) + '</td><td><code>' + esc(e.uid) + '</code></td></tr>' }).join('') + '</table></div>';
   h += '<div class="card"><h3>תנועה לפי שעה (אחרונות 24)</h3><table><tr><th>שעה</th><th>צפיות</th><th>ייחודיים</th></tr>'
     + j.traffic.hours.slice(0,24).map(function(x){ return '<tr><td>' + esc(x.hour) + '</td><td>' + N(x.pageviews) + '</td><td>' + N(x.unique_ips) + '</td></tr>' }).join('') + '</table></div>';
   h += '<p class="muted">נוצר ' + esc(j.generated) + ' · הנתונים הם אגרגטים + מזהי מבקר חתוכים ומגובבים בלבד — אין IP גולמי ואין מזהים מלאים.</p>';
   document.getElementById('app').innerHTML = h;
 })
 .catch(function(){ document.getElementById('app').textContent = '⛔ אין גישה — טוקן שגוי או שלא הוגדר HIT_READ_TOKEN באתר.' });
</script></body></html>`;

export default async function handler(request) {
  const url = new URL(request.url);
  if (request.method !== "GET") return new Response("method not allowed", { status: 405 });
  if (url.searchParams.get("data") !== "1") {
    return new Response(PAGE, { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow" } });
  }
  if (!authorized(request)) return new Response("forbidden", { status: 403, headers: { "Cache-Control": "no-store" } });

  const trafficStore = getStore("site-traffic");
  const { blobs } = await trafficStore.list({ prefix: "h:" });
  const hours = [];
  const todayIps = new Set(), pagePv = {};
  let todayPv = 0;
  const todayStr = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Jerusalem", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  for (const { key } of blobs.sort((a, b) => b.key.localeCompare(a.key))) {
    const data = await trafficStore.get(key, { type: "json" }).catch(() => null);
    if (!data) continue;
    hours.push({ hour: key.slice(2), pageviews: data.pv || 0, unique_ips: (data.ips || []).length });
    if (key.slice(2, 12) === todayStr) {
      todayPv += data.pv || 0;
      (data.ips || []).forEach((h) => todayIps.add(h));
      for (const [path, p] of Object.entries(data.pages || {})) pagePv[path] = (pagePv[path] || 0) + (p.pv || 0);
    }
  }
  const topPages = Object.entries(pagePv).map(([path, p]) => ({ path, pageviews: p })).sort((a, b) => b.pageviews - a.pageviews).slice(0, 8);
  const summaryRes = await (async () => {
    try {
      const s = getStore({ name: "site-traffic", consistency: "strong" });
      const { blobs: ob } = await s.list({ prefix: "online:" });
      let online = 0;
      for (const { key } of ob) { const v = await s.get(key, { type: "json" }).catch(() => null); if (v && Date.now() - v.t <= 120000) online++; }
      return online;
    } catch { return 0; }
  })();

  const starStore = getStore("star-counts");
  const { blobs: sb } = await starStore.list({ prefix: "s:" });
  const top = [];
  for (const { key } of sb) {
    const data = await starStore.get(key, { type: "json" }).catch(() => null);
    if (data && data.u && data.u.length) {
      const raw = key.slice(2), i = raw.indexOf(":");
      top.push({ kind: raw.slice(0, i), key: raw.slice(i + 1), count: data.u.length });
    }
  }
  top.sort((a, b) => b.count - a.count);

  const logStore = getStore("admin-log");
  const { blobs: lb } = await logStore.list({ prefix: "star:" });
  let starLog = [];
  for (const { key } of lb.sort((a, b) => b.key.localeCompare(a.key)).slice(0, 7)) {
    const day = await logStore.get(key, { type: "json" }).catch(() => null);
    if (Array.isArray(day)) starLog = starLog.concat(day);
  }

  return Response.json({
    generated: new Date().toISOString(),
    traffic: {
      today: { pageviews: todayPv, unique_ips: todayIps.size },
      hour: hours[0] || { pageviews: 0, unique_ips: 0 },
      top_pages: topPages,
      online: summaryRes,
      hours,
    },
    stars: { total_entities: top.length, top: top.slice(0, 50) },
    star_log: starLog.slice(-200),
  }, { headers: { "Cache-Control": "no-store" } });
}
