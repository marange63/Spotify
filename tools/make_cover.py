"""Generate the podcast cover art for 'Cautious Optimism Briefings'.

Spotify/Apple require a square RGB JPEG/PNG between 1400x1400 and 3000x3000.
We render 1500x1500 and keep it under a few hundred KB. Editorial dark theme
with a measured upward arc (the 'cautious optimism' motif).
"""
import os
from PIL import Image, ImageDraw, ImageFont

W = H = 1500
OUT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "cover.jpg")

INK_TOP = (9, 13, 24)        # deep near-black navy
INK_BOT = (24, 32, 58)       # slate navy
CREAM = (244, 241, 232)      # off-white text
GOLD = (216, 164, 58)        # amber accent
MUTE = (150, 165, 196)       # muted slate for tagline

FONT_DIR = r"C:\Windows\Fonts"
def font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)

img = Image.new("RGB", (W, H), INK_TOP)
px = img.load()
# vertical gradient
for y in range(H):
    t = y / (H - 1)
    r = int(INK_TOP[0] + (INK_BOT[0] - INK_TOP[0]) * t)
    g = int(INK_TOP[1] + (INK_BOT[1] - INK_TOP[1]) * t)
    b = int(INK_TOP[2] + (INK_BOT[2] - INK_TOP[2]) * t)
    for x in range(W):
        px[x, y] = (r, g, b)

draw = ImageDraw.Draw(img)

# Measured upward arc — a gently rising curve in the lower third, clear of all text.
pts = []
for i in range(0, 101):
    t = i / 100
    x = 150 + t * (W - 300)
    # ease-out rise: starts low-left, climbs to right, flattening (cautious)
    y = 1200 - (1 - (1 - t) ** 2) * 210
    pts.append((x, y))
draw.line(pts, fill=GOLD, width=12, joint="curve")
# a small marker dot at the leading (upper-right) end
ex, ey = pts[-1]
draw.ellipse([ex - 18, ey - 18, ex + 18, ey + 18], fill=GOLD)

# thin rule near top
draw.line([(150, 250), (W - 150, 250)], fill=(60, 74, 108), width=3)

# Title (Georgia Bold), two lines, upper area
title_f = font("georgiab.ttf", 168)
sub_f = font("georgiab.ttf", 168)
draw.text((150, 300), "Cautious", font=title_f, fill=CREAM)
draw.text((150, 490), "Optimism", font=title_f, fill=CREAM)
# 'Briefings' in gold to anchor the brand
draw.text((150, 680), "Briefings", font=font("georgiab.ttf", 168), fill=GOLD)

# small standfirst under title
sf_f = font("ariali.ttf", 46)
draw.text((156, 905), "Expert daily audio, one signal at a time.", font=sf_f, fill=MUTE)
# Tagline (Arial), bottom — below the arc
tag_f = font("arial.ttf", 50)
draw.text((152, 1320), "Economics · Markets · Technology · AI · Digital Assets",
          font=tag_f, fill=MUTE)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
img.save(OUT, "JPEG", quality=88, optimize=True)
print("wrote", OUT, os.path.getsize(OUT), "bytes")
