"""Draws the DMG window background (1x and @2x) in Lookhound's style:
warm dark brown, serif title, and an orange arrow from the app to Applications.
Run from the repo root: python packaging/make_dmg_background.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "src/lookhound/resources/fonts"
OUT = Path(__file__).resolve().parent

W, H = 640, 400                    # window size in points; make_dmg.py uses the same
APP_X, APPS_X, ICON_Y = 180, 460, 250

BG = (20, 19, 17)
INK = (238, 232, 222)
MUTED = (154, 145, 132)
ACCENT = (224, 154, 92)
LINE = (46, 43, 38)


def draw(scale):
    s = scale
    im = Image.new("RGB", (W * s, H * s), BG)
    d = ImageDraw.Draw(im)
    serif = ImageFont.truetype(str(FONTS / "InstrumentSerif-Regular.ttf"), 46 * s)
    serif_italic = ImageFont.truetype(str(FONTS / "InstrumentSerif-Italic.ttf"), 46 * s)
    sans = ImageFont.truetype(str(FONTS / "Geist.ttf"), 13 * s)
    small = ImageFont.truetype(str(FONTS / "Geist.ttf"), 10 * s)
    try:
        sans.set_variation_by_axes([400])
        small.set_variation_by_axes([600])
    except Exception:
        pass

    def centered(y, parts):
        """parts: [(text, font, colour)] drawn on one line, centred."""
        widths = [d.textlength(t, font=f) for t, f, _ in parts]
        x = (W * s - sum(widths)) / 2
        for (t, f, c), w in zip(parts, widths):
            d.text((x, y * s), t, font=f, fill=c)
            x += w

    # Eyebrow, title with an italic turn, and a kind line underneath.
    eyebrow = "WELCOME HOME"
    spaced = " ".join(eyebrow)
    centered(36, [(spaced, small, ACCENT)])
    centered(52, [("Bring the ", serif, INK), ("hound", serif_italic, INK), (" home.", serif, INK)])
    centered(114, [("Drag Lookhound into your Applications folder. It'll take care of the rest.", sans, MUTED)])

    # A soft, slightly curved arrow between the two icons.
    x0, x1, y = (APP_X + 78) * s, (APPS_X - 78) * s, (ICON_Y - 4) * s
    lift = 26 * s
    pts = []
    for i in range(41):
        t = i / 40
        x = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * ((x0 + x1) / 2) + t ** 2 * x1
        yy = (1 - t) ** 2 * y + 2 * (1 - t) * t * (y - lift) + t ** 2 * y
        pts.append((x, yy))
    for a, b in zip(pts[::2], pts[1::2]):            # dashed
        d.line([a, b], fill=ACCENT, width=3 * s)
    tip = pts[-1]
    d.polygon([tip, (tip[0] - 13 * s, tip[1] - 9 * s), (tip[0] - 10 * s, tip[1] + 8 * s)], fill=ACCENT)

    # Hairline and a quiet footer.
    d.line([(60 * s, 340 * s), ((W - 60) * s, 340 * s)], fill=LINE, width=max(1, s))
    centered(356, [("Free  ·  Private  ·  Works offline", small, MUTED)])
    return im


draw(1).save(OUT / "dmg-background.png")
draw(2).save(OUT / "dmg-background@2x.png")
print("wrote", OUT / "dmg-background.png")
