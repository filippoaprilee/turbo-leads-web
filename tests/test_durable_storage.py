import csv
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import turbo_scraper as core
from durable_storage import GitHubStorage, make_checkpoint, restore_checkpoint

JOB_ID = "web_" + "a" * 12 + "_approfondita_p10_autonoleggio_" + "b" * 10

class BackupTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            state, results = Path(td) / "stato", Path(td) / "risultati"
            base = state / JOB_ID
            base.mkdir(parents=True)
            results.mkdir()
            dbfile = base / "progress.sqlite3"
            with sqlite3.connect(str(dbfile)) as conn:
                conn.execute("CREATE TABLE searches(query TEXT PRIMARY KEY,status TEXT)")
                conn.execute("INSERT INTO searches VALUES('autonoleggio in Lecce, LE','done')")
            (base / "categories.txt").write_text("autonoleggio\n", encoding="utf-8")
            (base / "piano.json").write_text('{"mode":"italia"}', encoding="utf-8")
            (base / "locations").mkdir()
            (base / "locations" / "LE.json").write_text('{"cities":[]}', encoding="utf-8")
            out = results / (JOB_ID + ".csv")
            with out.open("w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f, delimiter=";").writerows([
                    ["Nome", "Comune", "Telefono", "Email"],
                    ["Autonoleggio Locale", "Lecce", "0832000000", ""]
                ])
            checkpoint = make_checkpoint(base, out, "pipps", JOB_ID)
            self.assertGreater(len(checkpoint), 200)
            with mock.patch.object(core, "STATE", state), mock.patch.object(core, "RESULTS", results):
                out.unlink()
                dbfile.unlink()
                manifest = restore_checkpoint(checkpoint, "pipps", expected_id=JOB_ID)
                self.assertEqual(manifest["plan_id"], JOB_ID)
                with sqlite3.connect(str(dbfile)) as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM searches WHERE status='done'").fetchone()[0], 1)
                self.assertIn("Autonoleggio Locale", out.read_text(encoding="utf-8-sig"))

    def test_other_user_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / JOB_ID
            base.mkdir()
            with sqlite3.connect(str(base / "progress.sqlite3")) as conn:
                conn.execute("CREATE TABLE t(x INTEGER)")
            data = make_checkpoint(base, Path(td) / "results.csv", "pipps", JOB_ID)
            with self.assertRaises(ValueError):
                restore_checkpoint(data, "another-user")

    def test_public_repository_rejected(self):
        storage = GitHubStorage("owner/project", "dummy")
        with mock.patch.object(storage, "_request", return_value={"private": False}):
            with self.assertRaisesRegex(RuntimeError, "PRIVATO"):
                storage.verify_private()

    def test_private_repository_accepted(self):
        storage = GitHubStorage("owner/project", "dummy")
        with mock.patch.object(storage, "_request", return_value={"private": True}):
            storage.verify_private()
        self.assertTrue(storage.verified)

    def test_zip_slip_rejected(self):
        manifest = {
            "version": 1, "user_tag": __import__("hashlib").sha256(b"pipps").hexdigest()[:16],
            "plan_id": JOB_ID
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("manifest.json", json.dumps(manifest))
            z.writestr("progress.sqlite3", b"fake")
            z.writestr("../outside.txt", b"x")
        with self.assertRaises(ValueError):
            restore_checkpoint(buffer.getvalue(), "pipps")

if __name__ == "__main__":
    unittest.main()
