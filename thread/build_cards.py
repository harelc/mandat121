#!/usr/bin/env python3
"""Build X/Twitter thread cards for המנדט ה־121 (1600x900 PNGs).

Design = the site's own system (index.html :root):
warm paper #f4efe3, ink #26221a, dusty ink-blue #476b9e, ochre #b98f1f,
sage-for #3a8a18 / terracotta-against #cf3f1d. Frank Ruhl Libre + Rubik.

Usage:  python3 thread/build_cards.py
Outputs thread/cards/*.png  (HTML sources kept alongside for tweaking)
"""
import json, math, subprocess, pathlib

ROOT = pathlib.Path(__file__).parent
OUT = ROOT / "cards"
OUT.mkdir(exist_ok=True)
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

FCOL = {'הליכוד':'#2563eb','יש עתיד':'#f97316','המחנה הממלכתי':'#38bdf8',
        'הציונות הדתית':'#22c55e','ש"ס':'#a78bfa','יהדות התורה':'#94a3b8',
        'ישראל ביתנו':'#f43f5e','עוצמה יהודית':'#dc2626','העבודה':'#fb7185',
        'רע"ם':'#84cc16','חד"ש-תע"ל':'#ef4444','נעם':'#d4a017','הימין הממלכתי':'#2dd4bf'}
SHORT = {'הליכוד':'הליכוד','יש עתיד':'יש עתיד',
         'התאחדות הספרדים שומרי תורה תנועתו של מרן הרב עובדיה יוסף זצ"ל':'ש"ס',
         'כחול לבן - המחנה הממלכתי':'המחנה הממלכתי',
         'הציונות הדתית בראשות בצלאל סמוטריץ\'':'הציונות הדתית',
         'יהדות התורה':'יהדות התורה','עוצמה יהודית בראשות איתמר בן גביר':'עוצמה יהודית',
         'ישראל ביתנו':'ישראל ביתנו','רע"ם':'רע"ם','חד"ש-תע"ל':'חד"ש-תע"ל',
         'העבודה':'העבודה','הימין הממלכתי':'הימין הממלכתי','נעם - בראשות אבי מעוז':'נעם'}

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Heebo:wght@400;600;700;800&family=Rubik:wght@700;800;900&family=Frank+Ruhl+Libre:wght@800;900&display=swap');
*{margin:0;box-sizing:border-box}
body{width:1600px;height:900px;overflow:hidden;direction:rtl;background:#f4efe3;color:#26221a;
 font-family:'Heebo',system-ui,sans-serif;padding:72px 84px;position:relative;
 background-image:radial-gradient(900px 420px at 85% -80px, rgba(71,107,158,.13), transparent 60%),radial-gradient(700px 380px at 8% -60px, rgba(185,143,31,.14), transparent 60%)}
body::after{content:'';position:absolute;inset:22px;border:3px double #c9b98f;border-radius:28px;pointer-events:none}
.serif{font-family:'Frank Ruhl Libre','Rubik',serif}
.brand{display:flex;align-items:center;gap:16px;font-family:'Rubik';font-weight:900;font-size:30px}
.brand em{font-style:normal;color:#476b9e;background:linear-gradient(transparent 64%,rgba(185,143,31,.42) 64%);padding:0 .08em}
.brand .url{margin-inline-start:auto;font-size:22px;color:#5f5847;font-weight:700;font-family:'Heebo'}
.kicker{display:inline-block;background:#e1e9f4;color:#476b9e;border:1px solid #9db1cf;font-weight:800;font-size:24px;padding:8px 20px;border-radius:999px}
h1{font-family:'Frank Ruhl Libre',serif;font-weight:900;font-size:96px;line-height:1.08;margin-top:28px}
h1 .blue{color:#476b9e} h1 .gold{background:linear-gradient(transparent 66%,rgba(185,143,31,.45) 66%)}
.sub{font-size:34px;color:#5f5847;line-height:1.5;max-width:1250px;margin-top:24px}
.sub b{color:#26221a}
.kpis{display:flex;gap:22px;margin-top:48px}
.kpi{background:#fbf8f1;border:1px solid #ddd2ba;border-radius:20px;box-shadow:0 10px 30px rgba(96,80,44,.13);padding:26px 34px;flex:1}
.kpi b{font-family:'Rubik';font-weight:900;font-size:58px;display:block;line-height:1}
.kpi span{font-size:23px;color:#857c66;font-weight:700}
.num{font-family:'Rubik';font-weight:900;font-size:300px;line-height:.95}
.foot{position:absolute;bottom:52px;inset-inline:84px;display:flex;justify-content:space-between;font-size:22px;color:#857c66;font-weight:700}
.chip{display:inline-flex;padding:7px 18px;border-radius:999px;font-size:23px;font-weight:800;border:1px solid #ddd2ba;background:#f1ebdb;color:#5f5847}
.bar{height:52px;border-radius:14px}
"""

def page(name, body, extra=""):
    html = f"<!DOCTYPE html><html lang='he' dir='rtl'><head><meta charset='utf-8'><style>{CSS}{extra}</style></head><body>{body}</body></html>"
    (OUT / f"{name}.html").write_text(html, encoding="utf-8")
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--window-size=1600,900", "--force-device-scale-factor=1",
                    f"--screenshot={OUT}/{name}.png", f"file://{OUT}/{name}.html"],
                   capture_output=True, timeout=60)
    print("built", OUT / f"{name}.png")

BRAND = """<div class="brand"><span>המנדט <em>ה־121</em></span><span class="url">mandat121.netlify.app</span></div>"""

# ---- 01 · cover ------------------------------------------------------------
page("01-cover", f"""
{BRAND}
<div style="display:flex;gap:40px;align-items:flex-start;margin-top:44px">
  <div style="flex:1">
    <span class="kicker">מעקב אזרחי חי אחרי הכנסת · חדש</span>
    <h1>מי הצביע איך,<br>על מה — <span class="blue">ולמה זה<br>משנה לכם.</span></h1>
    <p class="sub">ביום הבחירות חולקו <b>120 מנדטים</b>.<br>המנדט ה־121 נשאר אצל האזרחים — ועובד כל הקדנציה.</p>
  </div>
  <div style="text-align:center;padding-top:10px">
    <div class="num" style="color:#476b9e">121</div>
    <div style="font-size:26px;font-weight:800;color:#b98f1f">המנדט שלכם</div>
    <div style="font-size:22px;color:#857c66;font-weight:700;margin-top:6px">מול 120 שחולקו בקלפי</div>
  </div>
</div>
<div class="kpis">
  <div class="kpi"><b>7,557</b><span>הצבעות מליאה · כנסת 25</span></div>
  <div class="kpi"><b>7,491</b><span>הצעות חוק — גם כאלה שלא הגיעו להצבעה</span></div>
  <div class="kpi"><b>149</b><span>ח״כים שתועדו לאורך הקדנציה</span></div>
</div>
""")

# ---- 02 · plenum ------------------------------------------------------------
data = json.loads((ROOT / "faction_counts.json").read_text(encoding="utf-8"))
def short(name): return SHORT.get(name.strip(), name.strip())
seats = []
for name, n in data["counts"]:
    seats += [short(name)] * n
# semicircle wedges: positions sorted by angle, so each faction is a wedge
dots, R, cx, cy = [], [190, 275, 360, 445, 530], 800, 600
per_row = [14, 20, 26, 28, 32]
flat = []
for ri, cnt in enumerate(per_row):
    for i in range(cnt):
        a = math.pi - (i / (cnt - 1)) * math.pi if cnt > 1 else math.pi / 2
        flat.append((a, R[ri], cx + R[ri] * math.cos(a), cy - R[ri] * math.sin(a)))
flat.sort(key=lambda p: (-p[0], p[1]))
for (_, _, x, y), f in zip(flat, seats):
    dots.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="21" fill="{FCOL.get(f,"#999")}" stroke="#fbf8f1" stroke-width="3"/>')
legend = "".join(
    f'<span class="chip" style="gap:8px;font-size:20px;padding:6px 14px"><i style="width:14px;height:14px;border-radius:50%;background:{FCOL.get(short(n),"#999")};display:inline-block"></i>{short(n)} · {c}</span>'
    for n, c in data["counts"])
page("02-plenum", f"""
{BRAND}
<div style="display:flex;align-items:flex-end;justify-content:space-between;margin-top:30px">
  <div><span class="kicker">מפת המליאה</span>
  <h1 style="font-size:68px;margin-top:18px">ככה נראית הכנסת ה־25. <span class="blue">כל מושב לחיץ.</span></h1></div>
  <div style="font-size:22px;color:#857c66;font-weight:700;padding-bottom:10px">צבע = סיעה · לחיצה על מושב ← דף הח״כ/ית</div>
</div>
<svg width="1432" height="470" viewBox="0 0 1600 640" style="direction:ltr;display:block;margin:0 auto">{''.join(dots)}</svg>
<div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:14px;justify-content:center">{legend}</div>
""")

# ---- 03 · analytics ---------------------------------------------------------
cells = []
N = 14
for r in range(N):
    for c in range(N):
        bloc = (r < 7) == (c < 7)
        v = 92 if bloc else 12
        if (r < 7 and c >= 7) or (r >= 7 and c < 7):
            v = 10
        col = f"rgba(71,107,158,{v/100:.2f})" if bloc else f"rgba(207,63,29,{0.10 + (r+c)%3*0.05:.2f})"
        cells.append(f'<rect x="{c*36}" y="{r*36}" width="34" height="34" rx="5" fill="{col}"/>')
page("03-analytics", f"""
{BRAND}
<div style="display:flex;gap:56px;margin-top:40px">
  <div style="flex:1">
    <span class="kicker">ניתוח הצבעות · חדש</span>
    <h1 style="font-size:74px">הגושים, במספרים:<br><span class="blue">98.8% הסכמה</span> בתוך הגוש.<br><span style="color:#cf3f1d">8.9% בלבד</span> בין גושים.</h1>
    <p class="sub" style="font-size:29px">מטריצת הסכמה של כל 149 הח״כים שכיהנו —<br>כל תא לחיץ, ומראה את ההצבעות עצמן.</p>
    <div style="margin-top:30px"><span class="chip">כיסוי: 7,451 מתוך 7,536 הצבעות עם פירוט לפי ח״כ</span></div>
  </div>
  <div>
    <svg width="504" height="504" viewBox="0 0 504 504" style="direction:ltr;background:#fbf8f1;border:1px solid #ddd2ba;border-radius:20px;padding:0">{''.join(cells)}</svg>
    <div style="display:flex;justify-content:space-between;font-size:21px;color:#857c66;font-weight:700;margin-top:12px"><span>■ בתוך הגוש</span><span>■ בין גושים</span></div>
  </div>
</div>
""")

# ---- 04 · close vote --------------------------------------------------------
page("04-close-vote", f"""
{BRAND}
<span class="kicker" style="margin-top:36px">למה כל קול קובע</span>
<h1 style="font-size:76px">הצבעה אחת.<br>קול אחד הכריע.</h1>
<div style="background:#fbf8f1;border:1px solid #ddd2ba;border-radius:20px;box-shadow:0 10px 30px rgba(96,80,44,.13);padding:36px 42px;margin-top:40px">
  <div style="font-size:29px;font-weight:700;line-height:1.45">הצעת חוק שירותי תעופה (פיצוי וסיוע בשל ביטול טיסה) — 11.6.2025</div>
  <div style="display:flex;gap:14px;margin-top:28px;direction:ltr">
    <div class="bar" style="background:#3a8a18;width:50.5%"></div><div class="bar" style="background:#cf3f1d;width:49.5%"></div>
  </div>
  <div style="display:flex;justify-content:space-between;margin-top:18px;font-family:'Rubik';font-weight:900;font-size:44px">
    <span style="color:#3a8a18">51 בעד ✓</span><span style="color:#cf3f1d">50 נגד</span>
  </div>
  <div style="font-size:24px;color:#857c66;font-weight:700;margin-top:10px">באתר: מי הצביע איך, ח״כ־ח״כ — ואיך אתם הייתם מצביעים?</div>
</div>
<div class="foot"><span>מקור: נתוני ההצבעות הרשמיים של הכנסת (OData)</span><span>ההצבעות הצמודות ביותר ← באתר</span></div>
""")

# ---- 05 · search + CTA -------------------------------------------------------
page("05-search-cta", f"""
{BRAND}
<span class="kicker" style="margin-top:36px">חיפוש בפרוטוקולים</span>
<h1 style="font-size:78px">מה אמרו בוועדה?<br><span class="blue">עכשיו אפשר לחפש.</span></h1>
<div style="background:#fbf8f1;border:1px solid #ddd2ba;border-radius:999px;padding:26px 40px;margin-top:42px;font-size:32px;color:#857c66;box-shadow:0 10px 30px rgba(96,80,44,.13)">🔍 &nbsp;חפשו מילה או שם — בכל הפרוטוקולים…</div>
<div class="kpis">
  <div class="kpi"><b>268</b><span>פרוטוקולי ועדות שנסרקו</span></div>
  <div class="kpi"><b>418</b><span>ישיבות מליאה · דברי הכנסת</span></div>
  <div class="kpi"><b>~3M</b><span>מילים בחיפוש אחד</span></div>
</div>
<p class="sub" style="margin-top:44px;font-size:36px">כל חוק. כל הצבעה. כל ח״כ. <b>המנדט ה־121 שלכם — תתחילו לעקוב:</b></p>
<div style="font-family:'Rubik';font-weight:900;font-size:54px;color:#476b9e;margin-top:10px">mandat121.netlify.app</div>
""")
print("done →", OUT)
