#!/usr/bin/env python3
"""
jev buildathon tickets: one high-resolution PNG per attendee, and optionally email each one.

setup (once, from the repo root):
  uv venv .venv && uv pip install -p .venv playwright qrcode opencv-python-headless
  .venv/bin/playwright install chromium

render:
  .venv/bin/python render.py attendees.csv                  # → dest/<name>.png (paper, 3x, dark stage)
  .venv/bin/python render.py attendees.csv --theme dark
  .venv/bin/python render.py attendees.csv --bg transparent # ticket only, die-cut corners transparent
  .venv/bin/python render.py attendees.csv --scale 4        # print size

email (renders light + dark transparent tickets into dest/light and dest/dark, then sends both inline):
  .venv/bin/python render.py attendees.csv --send --dry-run             # sends nothing, writes dest/preview.eml
  .venv/bin/python render.py attendees.csv --send --test-to you@x.com   # real emails, but every one goes to you
  .venv/bin/python render.py attendees.csv --send                       # the real run

CSV: a `name` (or `participant name`) column, shown lowercase on the ticket; `--send` also needs `email`.
Files are named after the normalised name (Arjun Mehta → arjun-mehta.png; repeats get -2, -3).
Ticket numbers look random but are derived from the name, so a re-run gives everyone the same number.

Every QR (the event page) is decoded back from the finished PNG. A mismatch is reported and
the run exits non-zero (and with --send, nothing is sent), so a bad ticket never goes out.

Email goes through Gmail SMTP, logged in as --login with an app password, with From set to
--sender, a verified "Send mail as" address on that account. The app password is read from
$GMAIL_APP_PASSWORD, else from .gmail-app-password (gitignored), else asked for.
Pacing, so Google doesn't throttle or block the account:
  - a pause of --delay seconds (±30% jitter) between emails
  - a fresh connection every --batch emails, after a --batch-pause
  - temporary SMTP errors (4xx, dropped connections) back off 1, 2, 4, 8 min and retry
  - a daily-limit rejection stops the run; --max caps how many go out in one run
Every delivered email is appended to dest/sent.csv and a re-run skips them, so an interrupted
or stopped run is finished by running the same command again.
"""
import argparse, csv, datetime, getpass, hashlib, html, os, pathlib, random, re, smtplib, socket, ssl, sys, time
import unicodedata, urllib.parse
from collections import Counter
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import NamedTuple

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

SUBJECT = "your jev buildathon ticket"
DATE, TIME, VENUE = "sun 27 sep 2026", "3:00 – 6:30 pm ist", "lossfunk, indiranagar, bengaluru"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TEMPORARY = {421, 450, 451, 452, 454}


# ---------- tickets ----------

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


class Attendee(NamedTuple):
    row: int      # spreadsheet row number (header is row 1)
    name: str
    no: int       # ticket number
    slug: str     # file name without .png
    email: str


def read_attendees(path) -> list[Attendee]:
    """The CSV's attendees with their ticket numbers and file names; empty names are reported and skipped."""
    reader = csv.DictReader(open(path, newline="", encoding="utf-8-sig"))
    heads = reader.fieldnames or []
    col = next((h for h in heads if h and h.strip().lower() in ("name", "participant name")), None)
    if not col:
        sys.exit(f"csv needs a `name` or `participant name` column (found: {reader.fieldnames})")
    ecol = next((h for h in heads if h and h.strip().lower() in ("email", "email address", "e-mail")), None)
    seen, taken, out = set(), set(), []
    for i, r in enumerate(reader, 2):
        name = " ".join((r.get(col) or "").split())
        if not name:
            print(f"- row {i}: empty name, skipped"); continue
        no = ticket_no(name, taken)
        base = slugify(name) or f"attendee-{no:04d}"
        slug, k = base, 1
        while slug in seen:
            k += 1; slug = f"{base}-{k}"
        seen.add(slug)
        out.append(Attendee(i, name, no, slug, (r.get(ecol) or "").strip() if ecol else ""))
    return out


def render(people, out: pathlib.Path, theme, bg, scale, check) -> list[str]:
    """Writes out/<slug>.png for each attendee; returns the slugs whose QR didn't read back."""
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    qr = qr_svg(URL)
    failed = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--font-render-hinting=none", "--disable-lcd-text"])
        page = browser.new_page(viewport={"width": 1300, "height": 600}, device_scale_factor=scale)
        for _, name, no, slug, _ in people:
            q = {"name": name, "no": no, "theme": theme, "shot": "1"}
            if bg == "transparent": q["bg"] = "transparent"
            page.goto((HERE / "ticket.html").as_uri() + "?" + urllib.parse.urlencode(q))
            page.wait_for_function("window.__ready === true", timeout=20000)
            page.evaluate("s => document.querySelectorAll('[data-qr]').forEach(e => {e.innerHTML = s; e.dataset.injected = 1})", qr)
            target = page.locator("#ticket" if bg == "transparent" else "#stage")
            path = out / f"{slug}.png"
            target.screenshot(path=str(path), omit_background=(bg == "transparent"))
            if check:
                got = decode(path)
                if got != URL:
                    failed.append(slug); print(f"✕ {slug}: QR reads {got!r}, expected {URL!r}"); continue
            print(f"✓ #{no:04d}  {path.relative_to(out.parent)}" + ("  (qr ok)" if check else ""))
        browser.close()
    return failed


# ---------- email ----------

def build(sender, to, name, no, light: bytes, dark: bytes) -> EmailMessage:
    first = name.split()[0]
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, SUBJECT
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1].rstrip(">"))

    msg.set_content(
        f"hi {first},\n\n"
        f"you're in for jev buildathon on sunday. here's your ticket, #{no:04d}.\n\n"
        f"it comes in light and dark (the images are in this email). if you post about the\n"
        f"buildathon, share whichever you like and tag failproof ai and lossfunk so we see it.\n\n"
        f"{DATE} · {TIME} · {VENUE}\n{URL}\n\n"
        f"see you there,\nnivedit\n")

    lcid, dcid = make_msgid("light")[1:-1], make_msgid("dark")[1:-1]
    img = lambda cid, alt: (f'<img src="cid:{cid}" alt="{alt}" width="600" '
                            f'style="display:block;width:100%;max-width:600px;height:auto;margin:0 0 12px">')
    msg.add_alternative(f"""\
<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.55;color:#111;max-width:600px">
<p>hi {html.escape(first)},</p>
<p>you're in for jev buildathon on sunday. here's your ticket, #{no:04d}.</p>
<p>it comes in light and dark. if you post about the buildathon, share whichever you like and tag
failproof ai and lossfunk so we see it.</p>
{img(lcid, f"jev buildathon ticket #{no:04d}, light")}
{img(dcid, f"jev buildathon ticket #{no:04d}, dark")}
<p style="color:#666;font-size:13px">{html.escape(DATE)} · {html.escape(TIME)} · {html.escape(VENUE)}<br>
<a href="{URL}" style="color:#666">{URL}</a></p>
<p>see you there,<br>nivedit</p>
</div>""", subtype="html")
    part = msg.get_payload()[1]
    part.add_related(light, "image", "png", cid=f"<{lcid}>", disposition="inline", filename=f"ticket-{no:04d}-light.png")
    part.add_related(dark, "image", "png", cid=f"<{dcid}>", disposition="inline", filename=f"ticket-{no:04d}-dark.png")
    return msg


def app_password() -> str:
    if os.environ.get("GMAIL_APP_PASSWORD"):
        return os.environ["GMAIL_APP_PASSWORD"].strip()
    f = HERE / ".gmail-app-password"
    if f.exists():
        return f.read_text().strip()
    return getpass.getpass("gmail app password: ").strip()


class Mailer:
    """One SMTP connection, reopened when it drops or after every batch."""

    def __init__(self, login, password):
        self.login, self.password, self.smtp = login, password, None

    def connect(self):
        self.close()
        self.smtp = smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=120)
        try:
            self.smtp.login(self.login, self.password.replace(" ", ""))
        except smtplib.SMTPAuthenticationError as e:
            sys.exit(f"login failed for {self.login}: {e.smtp_error.decode(errors='replace')}\n"
                     "use an app password (myaccount.google.com/apppasswords), not the account password")

    def close(self):
        if self.smtp:
            try:
                self.smtp.quit()
            except Exception:
                pass
            self.smtp = None

    def send(self, msg: EmailMessage, to: str):
        """Send with retries; raises SMTPResponseException for a permanent rejection."""
        for attempt in range(5):
            try:
                if not self.smtp:
                    self.connect()
                self.smtp.send_message(msg, to_addrs=[to])
                return
            except smtplib.SMTPRecipientsRefused as e:
                code, text = next(iter(e.recipients.values()))
                if code not in TEMPORARY:
                    raise smtplib.SMTPResponseException(code, text)
                err = f"{code} {text.decode(errors='replace')}"
            except smtplib.SMTPResponseException as e:
                text = e.smtp_error.decode(errors="replace")
                if "5.4.5" in text or "limit exceeded" in text.lower() or e.smtp_code not in TEMPORARY:
                    raise
                err = f"{e.smtp_code} {text}"
            except (smtplib.SMTPServerDisconnected, socket.error) as e:
                err = f"connection: {e}"
            self.close()
            wait = 60 * 2 ** attempt
            print(f"  ! {err.strip()}; retrying in {wait // 60} min")
            time.sleep(wait)
        raise smtplib.SMTPResponseException(0, b"gave up after 5 attempts")


def send_all(a, people, check):
    out = pathlib.Path(a.out)
    light_dir, dark_dir, log = out / "light", out / "dark", out / "sent.csv"

    # ---- check everything before a single email goes out ----
    bad = [f"row {p.row} ({p.name}): " + (f"bad email {p.email!r}" if p.email else "no email")
           for p in people if not EMAIL_RE.match(p.email)]
    if bad:
        sys.exit("fix these first (nothing was rendered or sent):\n  " + "\n  ".join(bad))
    dupes = sorted(e for e, c in Counter(p.email.lower() for p in people).items() if c > 1)
    if dupes:
        print(f"! note: these addresses get more than one ticket: {', '.join(dupes)}")

    done = set()
    if log.exists() and not a.test_to:
        done = {(r["email"].lower(), r["file"]) for r in csv.DictReader(open(log, newline=""))}
    todo = [p for p in people if (p.email.lower(), p.slug) not in done]
    already = len(people) - len(todo)
    if a.max:
        todo = todo[:a.max]
    print(f"{len(people)} attendee(s), {already} already sent, {len(todo)} to send from {a.sender}"
          + (f", all to {a.test_to}" if a.test_to else ""))
    if not todo:
        return 0

    # ---- render only the tickets about to go out; any bad QR stops everything ----
    scale = a.scale or 2  # ~4 MB per email; 3 doubles that for no visible gain at email width
    failed = []
    for theme, d in (("paper", light_dir), ("dark", dark_dir)):
        failed += render(todo, d, theme, "transparent", scale, check)
    if failed:
        sys.exit(f"\n{len(failed)} ticket(s) failed the QR check, nothing was sent")

    est = len(todo) * a.delay + (len(todo) - 1) // a.batch * a.batch_pause
    print(f"\nsending takes about {est / 60:.0f} min" if est >= 90 else f"\nsending takes about {est:.0f}s")
    if a.dry_run:
        for p in todo:
            print(f"  #{p.no:04d}  {p.slug:<28} → {p.email}")
        p = todo[0]
        preview = out / "preview.eml"
        preview.write_bytes(build(a.sender, p.email, p.name, p.no, (light_dir / f"{p.slug}.png").read_bytes(),
                                  (dark_dir / f"{p.slug}.png").read_bytes()).as_bytes())
        print(f"\ndry run, nothing sent. first email written to {preview} (open it in a mail app)")
        return 0
    if not a.yes and input(f"send {len(todo)} email(s)? [y/N] ").strip().lower() != "y":
        sys.exit("cancelled")

    mailer = Mailer(a.login, app_password())
    mailer.connect()
    new_log = not log.exists()
    logf = None if a.test_to else open(log, "a", newline="")
    writer = logf and csv.writer(logf)
    if writer and new_log:
        writer.writerow(["sent_at", "email", "name", "no", "file", "message_id"])

    sent, failed = 0, []
    try:
        for n, p in enumerate(todo):
            if n and n % a.batch == 0:
                mailer.close()
                print(f"  … {a.batch_pause:g}s break after {n} emails")
                time.sleep(a.batch_pause)
            to = a.test_to or p.email
            msg = build(a.sender, to, p.name, p.no,
                        (light_dir / f"{p.slug}.png").read_bytes(), (dark_dir / f"{p.slug}.png").read_bytes())
            try:
                mailer.send(msg, to)
            except smtplib.SMTPResponseException as e:
                text = e.smtp_error.decode(errors="replace") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
                if "5.4.5" in text or "limit exceeded" in text.lower():
                    print(f"✕ google's daily sending limit reached after {sent} email(s). run the same command tomorrow to finish.")
                    break
                failed.append(p); print(f"✕ #{p.no:04d}  {to}: {e.smtp_code} {text.strip()}")
                continue
            sent += 1
            print(f"✓ #{p.no:04d}  {to}  ({n + 1}/{len(todo)})")
            if writer:
                writer.writerow([datetime.datetime.now().isoformat(timespec="seconds"), p.email,
                                 p.name, p.no, p.slug, msg["Message-ID"]])
                logf.flush()
            if n + 1 < len(todo) and (n + 1) % a.batch:
                time.sleep(a.delay * random.uniform(0.7, 1.3))
    except KeyboardInterrupt:
        print("\nstopped. run the same command again to carry on where it left off.")
    finally:
        mailer.close()
        if logf:
            logf.close()

    print(f"\n{sent} sent" + (f", {len(failed)} failed: " + ", ".join(p.slug for p in failed) if failed else "")
          + ("" if a.test_to else f". log: {log}"))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--theme", default="paper", choices=["paper", "dark"])
    ap.add_argument("--scale", type=float, help="device pixel ratio; default 3 (3540×1440 ticket), 2 with --send")
    ap.add_argument("--bg", default="dark", choices=["dark", "transparent"], help="stage around the ticket")
    ap.add_argument("--out", default=str(HERE / "dest"))
    ap.add_argument("--no-check", action="store_true", help="skip QR verification")
    em = ap.add_argument_group("email")
    em.add_argument("--send", action="store_true", help="render light + dark transparent tickets and email them")
    em.add_argument("--dry-run", action="store_true", help="render and check everything, write dest/preview.eml, send nothing")
    em.add_argument("--test-to", metavar="EMAIL", help="send every email to this address instead (not logged)")
    em.add_argument("--sender", default="Nivedit Jain <nivedit@befailproof.ai>", help="From: a verified send-as address")
    em.add_argument("--login", default="nivedit@exosphere.host", help="the Google account that owns --sender")
    em.add_argument("--delay", type=float, default=8, help="seconds between emails (±30%% jitter)")
    em.add_argument("--batch", type=int, default=40, help="emails per connection")
    em.add_argument("--batch-pause", type=float, default=90, help="seconds to rest between batches")
    em.add_argument("--max", type=int, help="send at most this many in this run")
    em.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    a = ap.parse_args()
    if (a.dry_run or a.test_to) and not a.send:
        ap.error("--dry-run and --test-to go with --send")

    people = read_attendees(a.csv)
    check = cv2 is not None and not a.no_check
    if not check and not a.no_check:
        print("! opencv not installed, QR verification skipped (pip install opencv-python-headless)")
    if a.send:
        sys.exit(send_all(a, people, check))

    out = pathlib.Path(a.out)
    failed = render(people, out, a.theme, a.bg, a.scale or 3, check)
    print(f"\n{len(people) - len(failed)} ticket(s) written to {out}/" + (f", {len(failed)} failed QR check" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
