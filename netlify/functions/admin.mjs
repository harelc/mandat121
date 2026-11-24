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
//
// Performance: this used to fetch every Blob sequentially (up to ~2,160
// hourly traffic buckets + every star/feedback blob, one await at a
// time) and return all of it. Now: keys are sorted and windowed BEFORE
// fetching (14 days of hours), and all gets run in parallel batches.

function authorized(request) {
  const token = process.env.HIT_READ_TOKEN;
  if (!token) return false;
  const given = request.headers.get("x-hit-token") || "";
  const a = Buffer.from(given), b = Buffer.from(token);
  return a.length === b.length && timingSafeEqual(a, b);
}

// Parallel Blob gets, ~32 at a time: fast, without hammering the store.
async function getMany(store, keys) {
  const out = new Array(keys.length).fill(null);
  for (let i = 0; i < keys.length; i += 32) {
    const batch = keys.slice(i, i + 32);
    const vals = await Promise.all(batch.map((k) => store.get(k, { type: "json" }).catch(() => null)));
    vals.forEach((v, j) => { out[i + j] = v; });
  }
  return out;
}

const PAGE = `<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>אדמין · המנדט ה־121</title>
<style>
*{box-sizing:border-box}body{font-family:system-ui,sans-serif;background:#f4efe3;color:#26221a;margin:0;line-height:1.55}
.wrap{max-width:1060px;margin:0 auto;padding:1.4rem 1rem 3rem}
h1{font-size:1.45rem;margin:.2rem 0 1rem}h3{margin:.1rem 0 .6rem;font-size:1.02rem}
.card{background:#fbf8f1;border:1px solid #ddd2ba;border-radius:16px;padding:1rem 1.2rem;margin-bottom:1rem;box-shadow:0 2px 10px rgba(96,80,44,.06)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.7rem;margin-bottom:1rem}
.kpi{background:#fbf8f1;border:1px solid #ddd2ba;border-radius:16px;padding:.8rem 1rem}
.kpi b{display:block;color:#476b9e;font-size:1.7rem;line-height:1.1;font-variant-numeric:tabular-nums}
.kpi span{color:#857c66;font-size:.8rem}
.grid2{display:grid;grid-template-columns:1fr;gap:1rem}@media(min-width:820px){.grid2{grid-template-columns:1fr 1fr}.grid2>.card{margin-bottom:0}}
.chart{display:flex;align-items:flex-end;gap:2px;height:150px;border-bottom:2px solid #ddd2ba;padding-top:.4rem}
.chart .col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:100%;min-width:0}
.chart .bar{background:linear-gradient(180deg,#6d8cb5,#476b9e);border-radius:4px 4px 0 0;min-height:2px}
.chart .bar.today{background:linear-gradient(180deg,#d3ab3e,#b98f1f)}
.xlabels{display:flex;gap:2px;color:#857c66;font-size:.62rem}
.xlabels span{flex:1;text-align:center;overflow:hidden;white-space:nowrap;min-width:0}
.hbar{display:grid;grid-template-columns:minmax(90px,1.2fr) 2fr 3rem;gap:.5rem;align-items:center;padding:.28rem 0;font-size:.85rem;border-bottom:1px dashed #e6dcc2}
.hbar:last-child{border-bottom:0}.hbar .tr{background:#efe7d0;border-radius:99px;height:.85rem;overflow:hidden}
.hbar .fl{height:100%;background:linear-gradient(90deg,#b98f1f,#d3ab3e);border-radius:99px}
.hbar .n{text-align:left;font-weight:800;font-variant-numeric:tabular-nums;color:#8a660a}
.hbar a{color:#476b9e;text-decoration:none}.hbar a:hover{text-decoration:underline}
table{border-collapse:collapse;width:100%;font-size:.85rem}td,th{border-bottom:1px solid #ddd2ba;padding:.35rem .5rem;text-align:right;vertical-align:top}
button{border:1px solid #b3a37c;background:#fffdf7;border-radius:999px;padding:.22rem .7rem;cursor:pointer;font-family:inherit;font-size:.8rem}
button:hover{background:#f1ebdb}.muted{color:#857c66;font-size:.82rem}code{background:#f1ebdb;padding:.05rem .35rem;border-radius:6px}
.pill{display:inline-block;border-radius:999px;padding:.05rem .55rem;font-size:.72rem;font-weight:700;background:#efe7d0}
.pill.pending{background:#f6ebc3;color:#8a660a}.pill.approved{background:#dcebd8;color:#2e5d33}.pill.rejected{background:#f3d9d7;color:#8c3a35}.pill.handled{background:#dbe4f0;color:#3d5a80}
details summary{cursor:pointer;color:#476b9e;font-weight:700;font-size:.88rem}
</style></head>
<body><div class="wrap"><h1>🎛️ אדמין — המנדט ה־121</h1><div id="app" class="muted">טוען…</div></div>
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
   // Vertical time-axis bar chart. items: chronological [{label, value, cls?}]
   var vchart = function(items){
     if(!items.length) return '<div class="muted">אין נתונים.</div>';
     var max = 1; items.forEach(function(x){ if(x.value>max) max=x.value });
     var cols = items.map(function(x){ var pct = Math.max(2, Math.round(x.value/max*100));
       return '<div class="col"><div class="bar ' + (x.cls||'') + '" style="height:' + pct + '%" title="' + esc(x.label) + ': ' + N(x.value) + '"></div></div>' }).join('');
     var step = Math.ceil(items.length/8);
     var labs = items.map(function(x,i){ return '<span>' + (i%step===0||i===items.length-1 ? esc(x.short||x.label) : '') + '</span>' }).join('');
     return '<div class="chart">' + cols + '</div><div class="xlabels">' + labs + '</div>';
   };
   var hbars = function(rows){ // rows: [{label(html), value}]
     if(!rows.length) return '<div class="muted">אין נתונים.</div>';
     var max = 1; rows.forEach(function(x){ if(x.value>max) max=x.value });
     return rows.map(function(x){ return '<div class="hbar"><span>' + x.label + '</span><span class="tr"><span class="fl" style="display:block;width:' + Math.max(3, Math.round(x.value/max*100)) + '%"></span></span><span class="n">' + N(x.value) + '</span></div>' }).join('');
   };
   var h = '';
   var fb = j.feedback || [];
   var pending = fb.filter(function(f){ return f.status==='pending' }).length;
   h += '<div class="kpis">'
     + '<div class="kpi"><b>' + N(j.traffic.today.pageviews) + '</b><span>צפיות היום</span></div>'
     + '<div class="kpi"><b>' + N(j.traffic.today.unique_ips) + '</b><span>מבקרים ייחודיים היום</span></div>'
     + '<div class="kpi"><b>' + N(j.traffic.online) + '</b><span>עכשיו באתר</span></div>'
     + '<div class="kpi"><b>' + N((j.traffic.hour||{}).pageviews) + '</b><span>צפיות בשעה האחרונה</span></div>'
     + '<div class="kpi"><b>' + N(j.stars.total_entities) + '</b><span>ישויות עם עוקבים</span></div>'
     + '<div class="kpi"><b>' + N(pending) + '</b><span>פניות משוב ממתינות</span></div></div>';
   var hoursAsc = (j.traffic.hours||[]).slice(0,24).slice().reverse();
   h += '<div class="card"><h3>📈 צפיות לפי שעה — 24 השעות האחרונות</h3>'
     + vchart(hoursAsc.map(function(x){ return { label: x.hour, short: x.hour.slice(11), value: x.pageviews } }))
     + '<div class="muted">ריחוף מעל עמודה מציג שעה ומספר מדויק. סך הכול בחלון: ' + N(hoursAsc.reduce(function(a,x){return a+x.pageviews},0)) + ' צפיות.</div></div>';
   h += '<div class="card"><h3>📊 צפיות לפי יום — 14 הימים האחרונים</h3>'
     + vchart((j.traffic.days||[]).map(function(x,i,arr){ return { label: x.day, short: x.day.slice(5), value: x.pageviews, cls: i===arr.length-1?'today':'' } })) + '</div>';
   h += '<div class="grid2"><div class="card"><h3>★ הכי נעקבים (' + N(j.stars.total_entities) + ' ישויות)</h3>'
     + hbars(j.stars.top.slice(0,15).map(function(s){ var kl = { mk:'ח״כ', bill:'חוק', committee:'ועדה', faction:'סיעה', topic:'נושא' }[s.kind] || s.kind; return { label: entLink(s.kind, s.key) + ' <span class="pill">' + esc(kl) + '</span>', value: s.count } })) + '</div>'
     + '<div class="card"><h3>🔥 דפים מובילים היום</h3>'
     + hbars((j.traffic.top_pages||[]).map(function(p){ return { label: '<span dir="ltr">' + esc(p.path||'/') + '</span>', value: p.pageviews } })) + '</div></div>';
   // Follow/unfollow activity per day, from the audit log
   var byDay = {};
   (j.star_log||[]).forEach(function(e){ var d = String(e.t||'').slice(0,10); if(!d) return; byDay[d] = byDay[d] || {f:0,u:0}; if(e.action==='follow') byDay[d].f++; else byDay[d].u++ });
   var days = Object.keys(byDay).sort();
   h += '<div class="card" style="margin-top:1rem"><h3>★ פעילות מעקב לפי יום — 7 הימים האחרונים ביומן</h3>'
     + vchart(days.map(function(d){ return { label: d + ' · נוספו למעקב ' + byDay[d].f + ' · הוסרו ממעקב ' + byDay[d].u, short: d.slice(5), value: byDay[d].f } }))
     + '<details><summary>יומן אחרון (' + Math.min(30,(j.star_log||[]).length) + ' מתוך ' + (j.star_log||[]).length + ')</summary><table><tr><th>זמן</th><th>פעולה</th><th>ישות</th><th>סה״כ</th></tr>'
     + (j.star_log||[]).slice(-30).reverse().map(function(e){ return '<tr><td><bdi dir="ltr">' + esc(String(e.t||'').replace('T',' ').slice(5,19)) + '</bdi></td><td>' + (e.action==='follow'?'★':'☆') + '</td><td>' + entLink(e.kind, e.key) + '</td><td>' + N(e.count) + '</td></tr>' }).join('') + '</table></details></div>';
   window.fbAct = function(id, action){ fetch('/.netlify/functions/feedback', { method:'POST', headers:{ 'Content-Type':'application/json', 'x-hit-token': tok }, body: JSON.stringify({ action: action, id: id }) }).then(function(r){ if(!r.ok) throw 0; location.reload() }).catch(function(){ alert('הפעולה נכשלה') }) };
   var fbLabel = { bug:'באג', idea:'רעיון', correction:'תיקון', testimonial:'המלצה' };
   var order = { pending:0, approved:1, handled:2, rejected:3 };
   var fbSorted = fb.slice().sort(function(a,b){ return (order[a.status]-order[b.status]) || String(b.ts).localeCompare(String(a.ts)) });
   h += '<div class="card"><h3>💬 משוב והמלצות (' + (fb.length===1?'פנייה אחת':fb.length + ' פניות') + ', ' + pending + ' ' + (pending===1?'ממתינה':'ממתינות') + ')</h3>' + (fb.length ? '<table><tr><th>זמן</th><th>סוג</th><th>טקסט</th><th>שם</th><th>פרסום?</th><th>סטטוס</th><th></th></tr>'
     + fbSorted.map(function(f){ return '<tr><td style="white-space:nowrap"><bdi dir="ltr">' + esc(String(f.ts||'').replace('T',' ').slice(5,16)) + '</bdi></td><td>' + esc(fbLabel[f.type]||f.type) + '</td><td>' + esc(f.text) + '</td><td>' + esc(f.name||'') + '</td><td>' + (f.type==='testimonial' ? (f.consent?'הסכים/ה':'לא הסכים/ה') : '—') + '</td><td><span class="pill ' + esc(f.status) + '">' + esc({ pending:'ממתינה', approved:'אושרה', rejected:'נדחתה', handled:'טופלה' }[f.status] || f.status) + '</span></td><td style="white-space:nowrap">' + (f.type==='testimonial' ? (f.status!=='approved' ? '<button onclick="fbAct(\\'' + esc(f.id) + '\\',\\'approve\\')">אישור</button> ' : '') + (f.status!=='rejected' ? '<button onclick="fbAct(\\'' + esc(f.id) + '\\',\\'reject\\')">דחייה</button>' : '') : (f.status!=='handled' ? '<button onclick="fbAct(\\'' + esc(f.id) + '\\',\\'handled\\')">סמן כטופלה</button>' : '<button onclick="fbAct(\\'' + esc(f.id) + '\\',\\'pending\\')">החזר לממתינות</button>')) + '</td></tr>' }).join('') + '</table>' : '<div class="muted">אין פניות עדיין.</div>') + '</div>';
   h += '<p class="muted">נוצר <bdi dir="ltr">' + esc(String(j.generated||'').replace('T',' ').slice(0,19)) + '</bdi> · חלון נתונים: 14 הימים האחרונים · הנתונים הם אגרגטים + מזהי מבקר חתוכים ומגובבים בלבד — אין IP גולמי ואין מזהים מלאים.</p>';
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

  const todayStr = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Jerusalem", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());

  // Traffic: window to the newest 14 days of hour buckets BEFORE fetching
  // (keys sort chronologically), then fetch in parallel batches.
  const trafficStore = getStore("site-traffic");
  const { blobs } = await trafficStore.list({ prefix: "h:" });
  const hourKeys = blobs.map((b) => b.key).sort((a, b) => b.localeCompare(a)).slice(0, 14 * 24);
  const hourVals = await getMany(trafficStore, hourKeys);
  const hours = [];
  const todayIps = new Set(), pagePv = {};
  const dayMap = new Map(); // day -> {pageviews, ips:Set}
  let todayPv = 0;
  hourKeys.forEach((key, i) => {
    const data = hourVals[i];
    if (!data) return;
    const stamp = key.slice(2), day = stamp.slice(0, 10);
    hours.push({ hour: stamp, pageviews: data.pv || 0, unique_ips: (data.ips || []).length });
    let d = dayMap.get(day);
    if (!d) { d = { day, pageviews: 0, ips: new Set() }; dayMap.set(day, d); }
    d.pageviews += data.pv || 0;
    (data.ips || []).forEach((x) => d.ips.add(x));
    if (day === todayStr) {
      todayPv += data.pv || 0;
      (data.ips || []).forEach((x) => todayIps.add(x));
      for (const [path, p] of Object.entries(data.pages || {})) pagePv[path] = (pagePv[path] || 0) + (p.pv || 0);
    }
  });
  const days = [...dayMap.values()].sort((a, b) => a.day.localeCompare(b.day))
    .map((d) => ({ day: d.day, pageviews: d.pageviews, unique_ips: d.ips.size }));
  const topPages = Object.entries(pagePv).map(([path, p]) => ({ path, pageviews: p })).sort((a, b) => b.pageviews - a.pageviews).slice(0, 8);

  // Independent stores: run them concurrently, each internally batched.
  const [online, top, starLog, feedback] = await Promise.all([
    (async () => {
      try {
        const { blobs: ob } = await trafficStore.list({ prefix: "online:" });
        const vals = await getMany(trafficStore, ob.map((b) => b.key));
        return vals.filter((v) => v && Date.now() - v.t <= 120_000).length;
      } catch { return 0; }
    })(),
    (async () => {
      const starStore = getStore("star-counts");
      const { blobs: sb } = await starStore.list({ prefix: "s:" });
      const vals = await getMany(starStore, sb.map((b) => b.key));
      const rows = [];
      sb.forEach(({ key }, i) => {
        const data = vals[i];
        if (data && data.u && data.u.length) {
          const raw = key.slice(2), k = raw.indexOf(":");
          rows.push({ kind: raw.slice(0, k), key: raw.slice(k + 1), count: data.u.length });
        }
      });
      return rows.sort((a, b) => b.count - a.count);
    })(),
    (async () => {
      const logStore = getStore("admin-log");
      const { blobs: lb } = await logStore.list({ prefix: "star:" });
      const keys = lb.map((b) => b.key).sort((a, b) => a.localeCompare(b)).slice(-7);
      const vals = await getMany(logStore, keys);
      const log = vals.flatMap((v) => (Array.isArray(v) ? v : []));
      return log.sort((a, b) => String(a.t || "").localeCompare(String(b.t || "")));
    })(),
    (async () => {
      try {
        const fs = getStore("feedback");
        const { blobs: fb } = await fs.list({ prefix: "f:" });
        const keys = fb.map((b) => b.key).sort((a, b) => b.localeCompare(a)).slice(0, 100);
        const vals = await getMany(fs, keys);
        return vals.filter(Boolean).sort((a, b) => String(b.ts || "").localeCompare(String(a.ts || "")));
      } catch { return []; }
    })(),
  ]);

  return Response.json({
    generated: new Date().toISOString(),
    traffic: {
      today: { pageviews: todayPv, unique_ips: todayIps.size },
      hour: hours[0] || { pageviews: 0, unique_ips: 0 },
      top_pages: topPages,
      online,
      hours,
      days,
    },
    stars: { total_entities: top.length, top: top.slice(0, 20) },
    star_log: starLog.slice(-60),
    feedback,
  }, { headers: { "Cache-Control": "no-store" } });
}
