"""Offline unit tests for upload_data.py and baseline-mode summarize_commit.py."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import summarize_commit
import upload_data


class UploadDataTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        (self.data / "k26_agenda").mkdir()
        (self.data / "protocols").mkdir()
        (self.data / "photos").mkdir()
        (self.data / "search_c26").mkdir()
        (self.data / "search_p26").mkdir()

        # Seed a minimal realistic data tree.
        (self.data / "k26_bills.json").write_text(
            json.dumps([{"id": 1, "name": "חוק ראשון", "status": "הונח"}], ensure_ascii=False),
            encoding="utf-8",
        )
        (self.data / "k26_agenda.json").write_text(
            json.dumps({"generated_at": "2026-11-24T08:00:00Z", "plenum": [], "committees": []}),
            encoding="utf-8",
        )
        (self.data / "k26_agenda" / "2026-11.json").write_text(
            json.dumps({"plenum": [], "committees": []}), encoding="utf-8"
        )
        (self.data / "k26_preview.json").write_text('{"parties":[]}', encoding="utf-8")
        (self.data / "k26_presence.json").write_text('{"days":{}}', encoding="utf-8")
        (self.data / "k25_bills.json").write_text('[{"id":99}]', encoding="utf-8")
        (self.data / "events.json").write_text("[]", encoding="utf-8")
        (self.data / "events_state.json").write_text('{"bills":{}}', encoding="utf-8")
        (self.data / "protocols_state.json").write_text("{}", encoding="utf-8")
        (self.data / "search_c26" / "manifest.json").write_text('{"sessions":[]}', encoding="utf-8")
        (self.data / "search_p26" / "manifest.json").write_text('{"sessions":[]}', encoding="utf-8")

        self.p_root = mock.patch.object(upload_data, "ROOT", self.root)
        self.p_data = mock.patch.object(upload_data, "DATA_DIR", self.data)
        self.p_root.start()
        self.p_data.start()
        self.addCleanup(self.p_root.stop)
        self.addCleanup(self.p_data.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_safe_data_path_rejects_traversal(self):
        for bad in ("../secret", "/etc/passwd", "index.html", "data/../../etc/passwd", "data"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    upload_data.safe_data_path(bad)
        ok = upload_data.safe_data_path("data/k26_bills.json")
        self.assertEqual(ok, (self.data / "k26_bills.json").resolve())

    def test_always_live_paths_scope(self):
        files, state = upload_data.always_live_paths(26)
        self.assertIn("data/k26_bills.json", files)
        self.assertIn("data/k26_agenda.json", files)
        self.assertIn("data/k26_agenda/2026-11.json", files)
        self.assertIn("data/events.json", files)
        self.assertNotIn("data/k26_preview.json", files)
        self.assertNotIn("data/k26_presence.json", files)
        self.assertNotIn("data/k25_bills.json", files)
        self.assertIn("data/events_state.json", state)
        self.assertIn("data/protocols_state.json", state)
        self.assertIn("data/search_c26/manifest.json", state)
        self.assertIn("data/search_p26/manifest.json", state)

    def test_bootstrap_noise_filter_and_meaningful_push(self):
        bucket = {}  # simulated R2 object store

        def fake_fetch(key, *, no_cache=False):
            return bucket.get(key)

        def fake_s3(method, key, body=b"", content_type=None, cache_control=None, **kw):
            if method == "PUT":
                bucket[key] = body
                return 200, b""
            raise AssertionError(f"unexpected method {method}")

        def fake_list():
            return {k: len(v) for k, v in bucket.items()}

        env = {
            "R2_ACCESS_KEY_ID": "test-key",
            "R2_SECRET_ACCESS_KEY": "test-secret",
            "R2_ENDPOINT": "https://example.r2.cloudflarestorage.com",
        }
        snap = self.root / "snap"
        changed_out = self.root / "changed.json"

        with mock.patch.dict(os.environ, env), \
             mock.patch.object(upload_data, "fetch_public", side_effect=fake_fetch), \
             mock.patch.object(upload_data, "s3_request", side_effect=fake_s3), \
             mock.patch.object(upload_data, "list_objects", side_effect=fake_list), \
             mock.patch.object(upload_data, "_git_modified_or_untracked_under_data", return_value=set()):
            # 1. Pull (empty bucket -> bootstrap) + snapshot
            self.assertEqual(upload_data.main(["pull", "--knesset", "26", "--snapshot-dir", str(snap)]), 0)
            self.assertTrue((snap / "data/k26_bills.json").is_file())

            # 2. Bootstrap push populates R2 and writes data-manifest.json
            self.assertEqual(
                upload_data.main(["push", "--knesset", "26", "--snapshot-dir", str(snap), "--changed-out", str(changed_out)]),
                0,
            )
            self.assertIn("data-manifest.json", bucket)
            self.assertIn("data/k26_bills.json", bucket)
            self.assertIn("state/data/events_state.json", bucket)
            self.assertEqual(json.loads(changed_out.read_text("utf-8")), [])

            # 3. Timestamp-only churn in k26_agenda.json -> skipped, 0 PUTs
            put_count_before = 0
            puts = []
            def track_s3(method, key, body=b"", *a, **kw):
                puts.append(key)
                return fake_s3(method, key, body, *a, **kw)

            (self.data / "k26_agenda.json").write_text(
                json.dumps({"generated_at": "2026-11-24T09:00:00Z", "plenum": [], "committees": []}),
                encoding="utf-8",
            )
            with mock.patch.object(upload_data, "s3_request", side_effect=track_s3):
                self.assertEqual(
                    upload_data.main(["push", "--knesset", "26", "--snapshot-dir", str(snap), "--changed-out", str(changed_out)]),
                    0,
                )
            self.assertEqual(len(puts), put_count_before)
            self.assertEqual(json.loads(changed_out.read_text("utf-8")), [])
            # Restored to baseline timestamp
            self.assertEqual(
                json.loads((self.data / "k26_agenda.json").read_text("utf-8"))["generated_at"],
                "2026-11-24T08:00:00Z",
            )

            # 4. Real new bill + new protocol -> only those 2 + manifest uploaded
            (self.data / "k26_bills.json").write_text(
                json.dumps([
                    {"id": 1, "name": "חוק ראשון", "status": "הונח"},
                    {"id": 2, "name": "חוק שני", "status": "הונח"},
                ], ensure_ascii=False),
                encoding="utf-8",
            )
            (self.data / "protocols" / "99999.json").write_text('{"turns":[]}', encoding="utf-8")
            puts.clear()
            with mock.patch.object(upload_data, "s3_request", side_effect=track_s3), \
                 mock.patch.object(
                     upload_data,
                     "_git_modified_or_untracked_under_data",
                     return_value={"data/k26_bills.json", "data/protocols/99999.json"},
                 ):
                self.assertEqual(
                    upload_data.main(["push", "--knesset", "26", "--snapshot-dir", str(snap), "--changed-out", str(changed_out)]),
                    0,
                )
            self.assertEqual(puts[-1], "data-manifest.json")
            self.assertEqual(set(puts[:-1]), {"data/k26_bills.json", "data/protocols/99999.json"})
            changed = json.loads(changed_out.read_text("utf-8"))
            self.assertEqual(changed, [["M", "data/k26_bills.json"], ["A", "data/protocols/99999.json"]])

            # 5. summarize_commit baseline mode works on the changed list
            orig_cwd = os.getcwd()
            try:
                os.chdir(self.root)
                body = summarize_commit.summarize_baseline(str(snap), changed)
                title = summarize_commit.title_for_baseline(str(snap), changed)
            finally:
                os.chdir(orig_cwd)
            self.assertIn("חוק שני", body)
            self.assertIn("Cloudflare R2", body)
            self.assertIn("data refresh", title)

    def test_invalid_json_refused(self):
        (self.data / "k26_bills.json").write_text("{broken json", encoding="utf-8")
        env = {
            "R2_ACCESS_KEY_ID": "k",
            "R2_SECRET_ACCESS_KEY": "s",
            "R2_ENDPOINT": "https://example.r2.cloudflarestorage.com",
        }
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(upload_data, "fetch_public", return_value=None), \
             mock.patch.object(upload_data, "_git_modified_or_untracked_under_data", return_value=set()):
            with self.assertRaises(ValueError):
                upload_data.main(["push", "--knesset", "26"])

    def test_cap_guard_refuses_oversized_upload(self):
        env = {
            "R2_ACCESS_KEY_ID": "k",
            "R2_SECRET_ACCESS_KEY": "s",
            "R2_ENDPOINT": "https://example.r2.cloudflarestorage.com",
        }
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(upload_data, "CAP_BYTES", 10), \
             mock.patch.object(upload_data, "fetch_public", return_value=None), \
             mock.patch.object(upload_data, "list_objects", return_value={}), \
             mock.patch.object(upload_data, "_git_modified_or_untracked_under_data", return_value=set()):
            rc = upload_data.main(["push", "--knesset", "26"])
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
