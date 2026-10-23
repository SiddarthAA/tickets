# tickets

Generates one high-res PNG ticket per attendee for **hacktober hack** (failproof ai × digitalocean × mlh × quad ai), [luma.com/nt5oal7h](https://luma.com/nt5oal7h).

## Setup (once)

```bash
uv venv .venv
uv pip install -p .venv playwright qrcode opencv-python-headless pillow
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

3. Tickets land in `dest/hacktober-hack/`, one per person: `Arjun Mehta` → `dest/hacktober-hack/arjun-mehta.png` (repeated names get `-2`, `-3`).

Each QR links to the event page and is scanned back from the finished PNG. If any fail, the run prints `✕` and exits non-zero, so don't send that batch.

## Options

| flag | default | |
|---|---|---|
| `--theme paper\|dark` | `paper` | ticket colours |
| `--bg dark\|transparent` | `dark` | dark border around the ticket, or ticket only |
| `--scale N` | `3` | 3 → 3804×1704 px; use `4` for print |
| `--out DIR` | `dest/hacktober-hack` | output folder |

## Sending

On WhatsApp, attach the PNG as a **Document**, not a photo, or it gets compressed and blurry.

## Emailing

`render.py --send` renders each attendee's ticket in light and dark (transparent) and sends both PNGs as attachments through listmonk's transactional API. The sender is `Failproof AI <ticket@befailproof.com>`; listmonk sends the email from its configured SMTP server.

**Once:**

1. In [listmonk](https://updates.befailproof.ai), create a **transactional** template. Set its body to the exact contents of [listmonk-ticket-template.html](listmonk-ticket-template.html) (`{{ Safe .Tx.Data.html }}`). The script checks the template type and body before sending. Note its ID.
2. Give a listmonk API user permission to read templates and send transactional messages. Set `LISTMONK_API_USER`, `LISTMONK_API_TOKEN`, and `LISTMONK_TEMPLATE_ID` in your shell or secret store. Do not put credentials in this repository.
3. Configure listmonk's SMTP server to permit `ticket@befailproof.com`; check SPF and DKIM for `befailproof.com`, and make sure replies to that address are handled. A dry run cannot verify mail authentication or delivery.

**Each batch:** the CSV needs an `email` column as well as `name`; a Luma guest CSV works as-is. CSVs other than the example are gitignored, since they hold attendee data.

```bash
.venv/bin/python render.py guests.csv --send --dry-run             # renders + checks everything, writes dest/hacktober-hack/preview.eml
.venv/bin/python render.py guests.csv --send --test-to you@x.com   # every email, sent to you instead; not logged
.venv/bin/python render.py guests.csv --send                       # the real run
```

Nothing is sent unless every row has a valid email and every ticket's QR scans. If the CSV has an `approval_status` column (Luma does), only `approved` guests are included.

More sheets later? Keep sending from the same folder (each event has its own, set by `EVENT` in `render.py`). `dest/hacktober-hack/sent.csv` covers every sheet, including the 115 tickets previously sent from Gmail. An address in that file is skipped regardless of which service sent it. Listmonk API acceptance is logged there; it does not prove final inbox delivery.

Before each API call, the script writes to `dest/hacktober-hack/attempts.csv`. If the call fails, times out, or the process dies before logging its response, that address is **held** on the next run and the command exits nonzero. Check listmonk's send activity for that recipient. Only after confirming no ticket was sent, remove that recipient's row from `attempts.csv` to permit a retry. Keep `sent.csv` intact. This avoids automatic duplicate sends after an uncertain response.

| flag | default | |
|---|---|---|
| `--delay S` | `20` | seconds between emails, ±30% jitter |
| `--batch N` / `--batch-pause S` | `20` / `180` | rest every N emails |
| `--max N` | none | send at most N this run |
| `--sender` | `ticket@befailproof.com` | From address, including display name |
| `--listmonk-url` | `https://updates.befailproof.ai` | listmonk endpoint |
| `--listmonk-template-id` | `LISTMONK_TEMPLATE_ID` | transactional template ID |
| `--scale N` | `2` with `--send` | tickets are compressed as PNG attachments |

The wording lives in `build()` in `render.py`; the event details are the `SUBJECT`, `DATE`, `TIME` and `VENUE` constants at the top.
