"""Record the README demo GIF and screenshots from a running app.

    python -m trendcatcher serve &          # app on :8517
    python scripts/record_demo.py           # writes docs/demo.gif + docs/*.png

Needs Playwright (`pip install playwright`) and a Chromium build. Set CHROMIUM to a
browser executable if `playwright install chromium` isn't possible on this machine.
Frames are captured with their real timestamps, so the GIF plays at real speed.
"""
import io
import os
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

BASE = os.environ.get("TC_URL", "http://localhost:8517")
OUT = Path(__file__).resolve().parent.parent / "docs"
VIEW = {"width": 1280, "height": 800}
GIF_WIDTH = 960
MAX_HOLD_MS = 1200

# A visible cursor: headless browsers don't draw one, and the demo needs to show clicks.
CURSOR_JS = """
addEventListener('DOMContentLoaded', () => {
  const c = document.createElement('div');
  c.style.cssText = 'position:fixed;z-index:99999;left:0;top:0;width:18px;height:18px;margin:-9px 0 0 -9px;' +
    'border-radius:50%;background:rgba(255,255,255,.9);box-shadow:0 0 0 1.5px rgba(0,0,0,.55),0 2px 8px rgba(0,0,0,.4);' +
    'pointer-events:none;transition:transform 120ms ease-out;';
  document.body.appendChild(c);
  addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
  addEventListener('mousedown', () => c.style.transform = 'scale(.7)', true);
  addEventListener('mouseup', () => c.style.transform = 'scale(1)', true);
});
"""


class Recorder:
    def __init__(self, page):
        self.page = page
        self.frames: list[tuple[Image.Image, float]] = []

    def capture(self, seconds: float):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            t = time.monotonic()
            img = Image.open(io.BytesIO(self.page.screenshot(type="jpeg", quality=92))).convert("RGB")
            self.frames.append((img, t))

    def glide(self, target, steps: int = 14):
        """Move the cursor smoothly to an element (recording on the way), then click it."""
        loc = self.page.locator(target).first if isinstance(target, str) else target
        box = loc.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.page.mouse.move(x, y, steps=steps)
        self.capture(0.25)
        self.page.mouse.down()
        self.capture(0.08)
        self.page.mouse.up()

    def save_gif(self, path: Path):
        imgs, durations = [], []
        for i, (img, t) in enumerate(self.frames):
            nxt = self.frames[i + 1][1] if i + 1 < len(self.frames) else t + 1.5
            # cap holds: a long gap means we were waiting on the server, not something worth watching
            durations.append(max(40, min(MAX_HOLD_MS, int((nxt - t) * 1000))))
            h = round(img.height * GIF_WIDTH / img.width)
            imgs.append(img.resize((GIF_WIDTH, h), Image.LANCZOS))
        # One shared palette keeps colours stable between frames and the file small. Build it from
        # frames across the whole recording, or accents seen only in some frames get washed out.
        sample = imgs[:: max(1, len(imgs) // 12)]
        strip = Image.new("RGB", (GIF_WIDTH, sum(im.height for im in sample)))
        y = 0
        for im in sample:
            strip.paste(im, (0, y))
            y += im.height
        palette = strip.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
        frames = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in imgs]
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True,
                       disposal=1)


def wait_feed(page):
    page.wait_for_selector(".card", timeout=120_000)
    page.wait_for_timeout(300)


def main():
    OUT.mkdir(exist_ok=True)
    launch = {"headless": True}
    if os.environ.get("CHROMIUM"):
        launch["executable_path"] = os.environ["CHROMIUM"]
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)

        # ---------------------------------------------------------------- demo GIF (dark)
        ctx = browser.new_context(viewport=VIEW, color_scheme="dark", device_scale_factor=1)
        ctx.add_init_script(CURSOR_JS)
        page = ctx.new_page()
        rec = Recorder(page)
        page.goto(BASE)
        wait_feed(page)
        page.mouse.move(640, 420)
        rec.capture(1.6)

        # filters respond instantly, client side
        rec.glide('#sourcePills .pill[data-source="tiktok"]')
        rec.capture(1.2)
        rec.glide('#sourcePills .pill[data-source="tiktok"]')
        rec.capture(0.6)

        # open a cross-platform trend with a saved brief: springs open, brief on the right
        head = page.locator(".card", has_text="Jailer 2").first.locator(".card-head")
        rec.glide(head)
        rec.capture(3.6)
        page.mouse.move(640, 760, steps=12)
        page.mouse.wheel(0, 260)
        rec.capture(1.6)
        page.mouse.wheel(0, -260)
        rec.capture(0.5)
        rec.glide(head)
        rec.capture(0.8)

        # pick a place: popover grows out of its button, search, local trends float to the top
        rec.glide("#locBtn")
        rec.capture(0.5)
        for ch in "New Y":
            page.keyboard.type(ch)
            rec.capture(0.07)
        rec.capture(0.4)
        page.keyboard.press("Enter")
        wait_feed(page)
        rec.capture(1.8)
        rec.glide("#localSwitch")
        rec.capture(2.2)
        rec.save_gif(OUT / "demo.gif")
        ctx.close()

        # ---------------------------------------------------------------- stills (2x)
        ctx = browser.new_context(viewport=VIEW, color_scheme="dark", device_scale_factor=2)
        page = ctx.new_page()
        page.goto(BASE)
        wait_feed(page)
        page.screenshot(path=OUT / "feed.png")
        page.locator(".card", has_text="Jailer 2").first.locator(".card-head").click()
        page.wait_for_selector(".card.open .angle", timeout=30_000)
        page.wait_for_timeout(900)
        page.locator(".card.open").evaluate("e => { e.scrollIntoView({block: 'start'}); scrollBy(0, -130); }")
        page.wait_for_timeout(300)
        page.screenshot(path=OUT / "trend-card.png")
        ctx.close()

        ctx = browser.new_context(viewport=VIEW, color_scheme="light", device_scale_factor=2)
        page = ctx.new_page()
        page.goto(f"{BASE}/?location=US-NY")
        wait_feed(page)
        page.screenshot(path=OUT / "local-new-york.png")
        ctx.close()
        browser.close()

    for f in ["demo.gif", "feed.png", "trend-card.png", "local-new-york.png"]:
        print(f"{f:22s} {(OUT / f).stat().st_size / 1e6:5.2f} MB")


if __name__ == "__main__":
    main()
