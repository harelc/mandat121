#!/usr/bin/env python3
"""Portrait Twitter hero (1200x1500) for the follow/stars/pipeline launch.
Palette = the site's own beige scheme (index.html :root variables)."""
from PIL import Image, ImageDraw, ImageFont
from bidi.algorithm import get_display

W, H = 1200, 1500
BG = (244, 239, 227); SURFACE = (251, 248, 241); SURF2 = (241, 235, 219)
LINE = (221, 210, 186); TEXT = (38, 34, 26); MID = (95, 88, 71); DIM = (133, 124, 102)
FOR = (58, 138, 24); AGAINST = (207, 63, 29); ABSTAIN = (201, 138, 4); ACCENT = (71, 107, 158)
GOLD = (201, 162, 39); GOLD_TX = (138, 102, 10)

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img, "RGBA")
for i in range(H):  # subtle vertical wash bg -> bg2
    t = i / H
    d.line([(0, i), (W, i)], fill=(244 - int(5 * t), 239 - int(7 * t), 227 - int(10 * t)))

FD = "examples/fonts/"
def F(sz, fam="Heebo", wt=600):
    f = ImageFont.truetype(FD + fam + ".ttf", sz)
    try: f.set_variation_by_axes([wt])
    except Exception: pass
    return f
STARF = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
def T(x, y, s, sz, fill=TEXT, anchor="ra", fam="Heebo", wt=600):
    # Heebo/Rubik/Frank Ruhl have no ★/☆ glyphs: composite those two chars
    # from Arial Unicode into the (already bidi-resolved) visual string.
    vis = get_display(s); font = F(sz, fam, wt)
    if "★" not in vis and "☆" not in vis:
        d.text((x, y), vis, font=font, fill=fill, anchor=anchor); return
    sfont = ImageFont.truetype(STARF, sz)
    parts = []
    for ch in vis:
        if ch in "★☆": parts.append((ch, sfont))
        elif parts and parts[-1][1] is font: parts[-1] = (parts[-1][0] + ch, font)
        else: parts.append((ch, font))
    total = sum(d.textlength(t, font=f) for t, f in parts)
    cx = x - total if anchor == "ra" else x - total / 2 if anchor == "ma" else x
    for t, f in parts:
        d.text((cx, y), t, font=f, fill=fill, anchor="la")
        cx += d.textlength(t, font=f)
def rr(box, rad, fill=None, outline=None, w=1):
    d.rounded_rectangle(box, rad, fill=fill, outline=outline, width=w)
def card(x0, y0, x1, y1):
    rr([x0 + 4, y0 + 6, x1 + 4, y1 + 6], 24, fill=(210, 198, 165))
    rr([x0, y0, x1, y1], 24, fill=SURFACE, outline=LINE, w=2)

M = 72; R = W - M  # right margin x (RTL start)

# header
rr([R - 330, 64, R, 122], 29, fill=(247, 236, 196), outline=GOLD, w=2)
T(R - 165, 76, "חדש במנדט ה־121", 32, GOLD_TX, "ma")
T(R, 152, "לעקוב אחרי הכנסת,", 86, TEXT, "ra", "FrankRuhlLibre", 900)
T(R, 248, "כמו שצריך.", 86, ACCENT, "ra", "FrankRuhlLibre", 900)
T(R, 356, "כוכבי מעקב עם מוני עוקבים חיים · ״שלי״ כמרכז בקרה", 31, MID)
T(R, 402, "ומסלול החקיקה המלא של כל חבר כנסת", 31, MID)

# --- Card 1: MK lawmaking pipeline histogram (the headline feature) ---
card(M, 478, R, 896)
T(R - 36, 512, "היסטוגרמת שלבי החקיקה של כל ח״כ", 36, TEXT, "ra", "Rubik", 800)
T(R - 36, 566, "הצעות חוק שחבר הכנסת יזמ/ה בכנסת 25 — לאן הן הגיעו?", 27, DIM)
segs = [(9, FOR), (7, (122, 182, 72)), (3, ABSTAIN), (62, (216, 198, 144)), (14, AGAINST), (5, (154, 143, 122))]
x = M + 36; bw = R - M - 72
for pct, col in segs:
    wpx = int(pct / 100 * bw)
    d.rounded_rectangle([x, 630, x + wpx, 678], 8, fill=col)
    x += wpx
lines = [("הפכו לחוק", "1,356", FOR), ("הגיעו להצבעת מליאה", "987", (122, 182, 72)),
         ("בוועדה / בהכנה לקריאה", "219", ABSTAIN), ("הונחו וממתינות", "10,239", (178, 160, 112)),
         ("נעצרו / נדחו", "2,838", AGAINST), ("מוזגו / הוסבו", "833", (154, 143, 122))]
yy = 706
for i, (label, n, col) in enumerate(lines):
    colx = R - 36 - (i % 2) * (bw // 2)
    if i == 2: yy += 0
    d.ellipse([colx - 18, yy + 7, colx, yy + 25], fill=col)
    T(colx - 30, yy, f"{label} · {n}", 26)
    if i % 2 == 1: yy += 44
T(R - 36, 838, "לחיצה על שלב בהיסטוגרמה מסננת את רשימת ההצעות של הח״כ", 24, ACCENT)

# --- Card 2: stars ---
card(M, 930, R, 1152)
T(R - 36, 964, "★ מעקב שעובד בשבילכם", 36, TEXT, "ra", "Rubik", 800)
rr([R - 250, 1022, R - 36, 1082], 30, fill=(247, 236, 196), outline=GOLD, w=2)
T(R - 143, 1034, "★  128 עוקבים", 29, GOLD_TX, "ma")
for i, lbl in enumerate(["חוק ☆", "ח״כ ☆", "ועדה ☆", "סיעה ☆"]):
    xx = R - 286 - i * 178
    rr([xx - 158, 1022, xx, 1082], 30, fill=SURF2, outline=LINE, w=2)
    T(xx - 79, 1036, lbl, 26, MID, "ma")
T(R - 36, 1104, "כולל סיעות — ומיון חדש בכל רשימה: ★ הכי נעקבים", 26, DIM)

# --- Card 3: command center tiles ---
card(M, 1186, R, 1362)
T(R - 36, 1218, "״שלי״ — מרכז הבקרה האישי", 36, TEXT, "ra", "Rubik", 800)
tiles = [("42", "הצבעות שסימנתי"), ("17", "במעקב שלי ★"), ("87%", "הכי כמוני"), ("12", "עכשיו באתר")]
tw = (bw - 24) // 2
for i, (n, lbl) in enumerate(tiles[:2]):
    tx = R - 36 - i * (tw + 24)
    rr([tx - tw, 1276, tx, 1338], 16, fill=SURF2, outline=LINE, w=1)
    T(tx - 18, 1286, n, 34, ACCENT)
    T(tx - 90, 1292, lbl, 23, MID)

# footer
T(R, 1406, "mandat121.netlify.app", 34, ACCENT)
T(M, 1414, "חינם · בלי הרשמה · המעקב נשמר רק בדפדפן שלכם", 25, DIM, "la")

img.save("/Users/harel/Developer/biapia/examples/twitter-hero.png")
print("saved")
