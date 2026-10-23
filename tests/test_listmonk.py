import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import render


class ListmonkTests(unittest.TestCase):
    def test_transactional_request_has_personalized_html_and_both_pngs(self):
        msg = render.build(render.LISTMONK_SENDER, "guest@example.com", "Asha", 1234,
                           inline_images=False)
        calls = []

        def request(path, body=None, content_type=None):
            calls.append((path, body, content_type))
            return {"data": True}

        client = render.Listmonk("https://example.com", "user", "token", 7)
        client.request = request
        client.send("guest@example.com", msg, [(b"light-png", "light.png"),
                                               (b"dark-png", "dark.png")])
        path, body, content_type = calls[0]
        self.assertEqual(path, "/api/tx")
        self.assertIn("multipart/form-data", content_type)
        self.assertIn(b'filename="light.png"', body)
        self.assertIn(b'filename="dark.png"', body)
        self.assertIn(b"light-png", body)
        self.assertIn(b"dark-png", body)
        data = json.loads(body.split(b"\r\n\r\n", 1)[1].split(b"\r\n--ticket-", 1)[0])
        self.assertEqual(data["subscriber_emails"], ["guest@example.com"])
        self.assertEqual(data["subscriber_mode"], "external")
        self.assertEqual(data["template_id"], 7)
        self.assertEqual(data["from_email"], render.LISTMONK_SENDER)
        self.assertIn("tickets are attached", data["data"]["html"])
        self.assertNotIn("cid:", data["data"]["html"])
        self.assertIn("hi Asha", data["altbody"])

    def test_legacy_sends_and_unresolved_attempts_are_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            sheet = out / "guests.csv"
            with sheet.open("w", newline="") as file:
                writer = csv.writer(file)
                writer.writerows([["name", "email"], ["Old", "old@example.com"],
                                  ["Uncertain", "uncertain@example.com"],
                                  ["New", "new@example.com"]])
            with (out / "sent.csv").open("w", newline="") as file:
                writer = csv.writer(file)
                writer.writerows([["sent_at", "email", "name", "no", "file", "message_id"],
                                  ["yesterday", "old@example.com", "Old", 1111, "old", "legacy-id"]])
            with (out / "attempts.csv").open("w", newline="") as file:
                writer = csv.writer(file)
                writer.writerows([["attempted_at", "email", "name", "no", "request_id"],
                                  ["today", "uncertain@example.com", "Uncertain", 2222, "id"]])

            sent = []
            args = SimpleNamespace(out=directory, test_to=None, max=None, sender=render.LISTMONK_SENDER,
                                   scale=2, delay=0, batch=20, batch_pause=0, dry_run=False, yes=True,
                                   listmonk_url="https://example.com", listmonk_template_id=7)
            with patch.object(render, "render", return_value=[]), \
                 patch.object(render, "email_png", return_value=b"png"), \
                 patch.object(render.Listmonk, "verify_template"), \
                 patch.object(render.Listmonk, "send", side_effect=lambda to, msg, files: sent.append(to)), \
                 patch.dict(render.os.environ, {"LISTMONK_API_USER": "user", "LISTMONK_API_TOKEN": "token"}):
                result = render.send_all(args, render.read_attendees(sheet), check=False)
                second_result = render.send_all(args, render.read_attendees(sheet), check=False)
            self.assertEqual(result, 1)  # the uncertain address still needs review
            self.assertEqual(second_result, 1)
            self.assertEqual(sent, ["new@example.com"])
            with (out / "sent.csv").open() as file:
                self.assertEqual(len(list(csv.DictReader(file))), 2)


if __name__ == "__main__":
    unittest.main()
