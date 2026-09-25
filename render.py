#!/usr/bin/env python3
"""
jev buildathon ticket renderer: one high-resolution PNG per attendee.

setup (once, from the repo root):
  uv venv .venv && uv pip install -p .venv playwright qrcode opencv-python-headless
  .venv/bin/playwright install chromium

usage:
  .venv/bin/python render.py attendees.csv                  # → dest/<name>.png (paper, 3x, dark stage)
  .venv/bin/python render.py attendees.csv --theme dark
  .venv/bin/python render.py attendees.csv --bg transparent # ticket only, die-cut corners transparent
  .venv/bin/python render.py attendees.csv --scale 4        # print size

CSV: a `name` (or `participant name`) column; shown lowercase on the ticket.
Files are named after the normalised name (Arjun Mehta → arjun-mehta.png; repeats get -2, -3).
Ticket numbers look random but are derived from the name, so a re-run gives everyone the same number.

Every QR (the event page) is decoded back from the finished PNG. A mismatch is reported and
the run exits non-zero, so a bad ticket never goes out.
"""
import argparse, csv, hashlib, pathlib, re, sys, unicodedata, urllib.parse

from playwright.sync_api import sync_playwright

try:
    import qrcode, qrcode.image.svg
except ImportError:
    sys.exit("missing dependency: pip install qrcode")
try:
    import cv2
except ImportError:
    cv2 = None

HERE = pathlib.Path(__file__).resolve().parent
URL = "https://befailproof.ai/events/jev-buildathon/"


def qr_svg(data: str) -> str:
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, border=0,
                      error_correction=qrcode.constants.ERROR_CORRECT_M)
    return re.sub(r'(width|height)="[^"]*"', "", img.to_string(encoding="unicode"), count=2)


def decode(path: pathlib.Path):
    img = cv2.imread(str(path))
    if img is None:
        return None
    det = cv2.QRCodeDetector()
    for candidate in (img, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 255 - cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)):
        val, pts, _ = det.detectAndDecode(candidate)
        if val:
            return val
    return None


def slugify(name: str) -> str:
    """José  D'Souza → jose-d-souza"""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def ticket_no(name: str, taken: set) -> int:
    """1–9999 from a hash of the name; on a clash, the next free number."""
    n = int(hashlib.sha256(name.lower().encode()).hexdigest(), 16) % 9999 + 1
    while n in taken:
        n = n % 9999 + 1
    taken.add(n)
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--theme", default="paper", choices=["paper", "dark"])
    ap.add_argument("--scale", type=float, default=3, help="device pixel ratio (3 → 3540×1440 for the ticket)")
    ap.add_argument("--bg", default="dark", choices=["dark", "transparent"], help="stage around the ticket")
    ap.add_argument("--out", default=str(HERE / "dest"))
    ap.add_argument("--no-check", action="store_true", help="skip QR verification")
    a = ap.parse_args()

    reader = csv.DictReader(open(a.csv, newline="", encoding="utf-8-sig"))
    col = next((h for h in reader.fieldnames or [] if h and h.strip().lower() in ("name", "participant name")), None)
    if not col:
        sys.exit(f"csv needs a `name` or `participant name` column (found: {reader.fieldnames})")
    rows = list(reader)
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    check = cv2 is not None and not a.no_check
    if not check and not a.no_check:
        print("! opencv not installed, QR verification skipped (pip install opencv-python-headless)")

    qr = qr_svg(URL)
    seen, taken, failed, done = set(), set(), [], 0
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--font-render-hinting=none", "--disable-lcd-text"])
        page = browser.new_page(viewport={"width": 1300, "height": 600}, device_scale_factor=a.scale)
        for i, r in enumerate(rows, 2):  # spreadsheet row numbers (header is row 1)
            name = " ".join((r.get(col) or "").split())
            if not name:
                print(f"- row {i}: empty name, skipped"); continue
            no = ticket_no(name, taken)
            q = {"name": name, "no": no, "theme": a.theme, "shot": "1"}
            if a.bg == "transparent": q["bg"] = "transparent"
            page.goto((HERE / "ticket.html").as_uri() + "?" + urllib.parse.urlencode(q))
            page.wait_for_function("window.__ready === true", timeout=20000)
            page.evaluate("s => document.querySelectorAll('[data-qr]').forEach(e => {e.innerHTML = s; e.dataset.injected = 1})", qr)
            base = slugify(name) or f"attendee-{no:04d}"
            slug, k = base, 1
            while slug in seen:
                k += 1; slug = f"{base}-{k}"
            seen.add(slug)
            target = page.locator("#ticket" if a.bg == "transparent" else "#stage")
            path = out / f"{slug}.png"
            target.screenshot(path=str(path), omit_background=(a.bg == "transparent"))
            if check:
                got = decode(path)
                if got != URL:
                    failed.append(slug); print(f"✕ {slug}: QR reads {got!r}, expected {URL!r}"); continue
            done += 1
            print(f"✓ #{no:04d}  {slug}.png" + ("  (qr ok)" if check else ""))
        browser.close()

    print(f"\n{done} ticket(s) written to {out}/" + (f", {len(failed)} failed QR check" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
