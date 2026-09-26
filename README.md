# tickets

Generates one high-res PNG ticket per attendee for **jev buildathon** (failproof ai × lossfunk).

## Setup (once)

```bash
uv venv .venv
uv pip install -p .venv playwright qrcode opencv-python-headless
.venv/bin/playwright install chromium
```

## Generate

1. Make a CSV with a `name` (or `participant name`) column:

   ```csv
   name
   Arjun Mehta
   Priya Raghavan
   ```

2. Run:

   ```bash
   .venv/bin/python render.py attendees.csv
   ```

3. Tickets land in `dest/`, one per person: `Arjun Mehta` → `dest/arjun-mehta.png` (repeated names get `-2`, `-3`).

Each QR links to the event page and is scanned back from the finished PNG. If any fail, the run prints `✕` and exits non-zero, so don't send that batch.

## Options

| flag | default | |
|---|---|---|
| `--theme paper\|dark` | `paper` | ticket colours |
| `--bg dark\|transparent` | `dark` | dark border around the ticket, or ticket only |
| `--scale N` | `3` | 3 → 3804×1704 px; use `4` for print |
| `--out DIR` | `dest` | output folder |

## Sending

On WhatsApp, attach the PNG as a **Document**, not a photo, or it gets compressed and blurry.

## Emailing

`render.py --send` renders each attendee's ticket in light and dark (transparent) and emails both inline from `nivedit@befailproof.ai` via Gmail SMTP, so people can share them.

**Once:**

1. Turn on 2-Step Verification for `nivedit@exosphere.host` (Workspace admins: Admin console → Security → 2-Step Verification must allow it).
2. Create an app password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) and save it in `.gmail-app-password` (gitignored).
3. `nivedit@befailproof.ai` must be a verified *Send mail as* address on that account (Gmail → Settings → Accounts). Set up DKIM for `befailproof.ai` in the Admin console so the mail doesn't land in spam.

**Each batch:** the CSV needs an `email` column as well as `name`; a Luma guest CSV works as-is. CSVs other than the example are gitignored, since they hold attendee data.

```bash
.venv/bin/python render.py guests.csv --send --dry-run             # renders + checks everything, writes dest/preview.eml
.venv/bin/python render.py guests.csv --send --test-to you@x.com   # every email, sent to you instead
.venv/bin/python render.py guests.csv --send                       # the real run
```

Nothing is sent unless every row has a valid email and every ticket's QR scans. It waits about 8s between emails with a longer break every 40, and backs off and retries when Gmail pushes back. Delivered emails are logged to `dest/sent.csv` and skipped on re-runs, so after a crash, Ctrl-C or Google's daily limit (about 2,000/day on Workspace) you just run the same command again.

| flag | default | |
|---|---|---|
| `--delay S` | `8` | seconds between emails, ±30% jitter |
| `--batch N` / `--batch-pause S` | `40` / `90` | reconnect and rest every N emails |
| `--max N` | none | send at most N this run |
| `--sender` / `--login` | befailproof.ai / exosphere.host | From address / account that owns it |
| `--scale N` | `2` with `--send` | about 4 MB per email |

The wording lives in `build()` in `render.py`; the event details are the `SUBJECT`, `DATE`, `TIME` and `VENUE` constants at the top.
