#!/usr/bin/env python3
"""Animated version of the Twitter hero (GIF, 800x1000 from the 1200x1500
static): pipeline bar sweeps in, activity-feed rows arrive one by one,
then the star counter ticks up. Loops. Static PNG stays the fallback."""
from PIL import Image, ImageDraw

SRC = Image.open("examples/twitter-hero.png").convert("RGB")  # 1200x1500 final frame
W, H = SRC.size
CARD = (251, 248, 241)   # --surface, matches card fill
SURF2 = (241, 235, 219)  # feed row fill

frames = []

def base():
    return SRC.copy()

# 1) Pipeline bar sweep: cover the bar, reveal left->right in 8 steps.
BAR = (108, 630, 1128, 678)
for i in range(9):
    im = base(); d = ImageDraw.Draw(im)
    x0 = BAR[0] + int((BAR[2] - BAR[0]) * i / 8)
    d.rectangle([x0, BAR[1], BAR[2], BAR[3]], fill=CARD)
    frames.append(im)

# 2) Feed rows arrive: rows are at y=1196+i*52 (x 102..1170, h 42).
ROWS = [(102, 1196 + i * 52, 1170, 1196 + i * 52 + 42) for i in range(4)]
for shown in range(5):
    im = base(); d = ImageDraw.Draw(im)
    for r in ROWS[shown:]:
        d.rounded_rectangle(r, 12, fill=CARD)
    frames.append(im)

# 3) Hold the finished hero.
frames.append(base())

out = [f.resize((800, 1000), Image.LANCZOS) for f in frames]
durations = [260] * 9 + [420] * 5 + [2600]
out[0].save("examples/twitter-hero.gif", save_all=True, append_images=out[1:],
            duration=durations, loop=0, optimize=True)
print("saved examples/twitter-hero.gif", len(out), "frames")
