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
