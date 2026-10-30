import json
import os
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests


_import_dir = TemporaryDirectory()
unittest.addModuleCleanup(_import_dir.cleanup)
with (
    patch("dotenv.load_dotenv"),
    patch.dict(
        os.environ,
        {
            "PLATFORM": "twitch",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_CHANNEL_ID": "test-channel",
            "DB_PATH": str(Path(_import_dir.name) / "recorder.db"),
            "SEGMENTS_DIR": str(Path(_import_dir.name) / "segments"),
        },
        clear=True,
    ),
):
    from app.config import Config
    from app.database import Database, db
    from app.recorder import Recorder
    from app.telegram_sender import TelegramSender
    from app.uploader import UploadWorker

unittest.addModuleCleanup(db.conn.close)


class GroupUploadTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        config = patch.multiple(
            Config,
            DB_PATH=str(self.root / "recorder.db"),
            SEGMENTS_DIR=str(self.root),
            TELEGRAM_SECOND_CHANNEL_ID=None,
            TELEGRAM_UPLOAD_MODE="video",
        )
        config.start()
        self.addCleanup(config.stop)
        self.db = Database()
        self.addCleanup(self.db.conn.close)
        self.sender = TelegramSender()
        self.sender._generate_thumbnail = Mock(return_value=None)
        self.sender._get_video_duration = Mock(return_value=None)
        self.worker = UploadWorker()
        dependencies = patch.multiple("app.uploader", db=self.db, telegram=self.sender)
        dependencies.start()
        self.addCleanup(dependencies.stop)
        worker = patch.multiple("app.recorder", uploader=self.worker, db=self.db)
        worker.start()
        self.addCleanup(worker.stop)
        self.recorder = Recorder()
        self.recorder.check_disk_space = Mock()
        self.recorder._stop_pipeline = Mock()
        self.recorder.build_caption = Mock(return_value="caption")
        self.files = []
        for i in range(10):
            path = self.root / f"session_{i}.mp4"
            path.write_bytes(b"video")
            self.files.append((str(path), "caption"))
        self.response = Mock()
        self.response.json.return_value = {
            "result": [{"message_id": i + 1} for i in range(10)]
        }
        network = patch("requests.post", return_value=self.response)
        self.post = network.start()
        self.addCleanup(network.stop)
        sleep = patch("app.telegram_sender.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def test_timeout_does_not_resend_album(self):
        self.post.side_effect = [requests.ReadTimeout(), self.response]

        result = self.sender.upload_media_group(self.files, "test-channel", "video")

        self.assertIsNone(result)
        self.post.assert_called_once()
        self.sleep.assert_not_called()

    def test_unreadable_success_response_does_not_resend_album(self):
        self.response.json.side_effect = ValueError("response lost")

        result = self.sender.upload_media_group(self.files, "test-channel", "video")

        self.assertIsNone(result)
        self.post.assert_called_once()
        self.sleep.assert_not_called()

    def test_failed_flush_stops_without_progress(self):
        self.recorder._upload_group_batch = Mock(
            side_effect=[set(), AssertionError("batch retried")]
        )
        group = self.files.copy()

        self.recorder._flush_group_if_needed(group, set())

        self.recorder._upload_group_batch.assert_called_once()
        self.assertEqual(group, self.files)

    def test_timeout_is_not_retried_by_later_polls_or_finalization(self):
        self.post.side_effect = requests.ReadTimeout()
        group = self.files.copy()
        uploaded = set()

        self.recorder._flush_group_if_needed(group, uploaded)
        self.recorder._flush_group_if_needed(group, uploaded)
        self.recorder.check_disk_space.side_effect = RuntimeError("low disk")
        self.recorder._flush_group_if_needed(group, uploaded)
        self.recorder._finalize_stream(group, uploaded, "session")

        self.post.assert_called_once()
        self.assertFalse(uploaded)
        self.assertEqual(group, self.files)
        self.assertTrue(all(Path(path).exists() for path, _ in self.files))
        self.assertFalse(self.db.get_uploaded_files())

    def test_attempt_survives_new_database_and_worker(self):
        self.post.side_effect = requests.ConnectionError()
        self.assertFalse(self.worker.upload_group(self.files))
        replacement_db = Database()
        self.addCleanup(replacement_db.conn.close)

        with patch("app.uploader.db", replacement_db):
            self.assertFalse(UploadWorker().upload_group(self.files))

        self.post.assert_called_once()
        self.assertTrue(all(Path(path).exists() for path, _ in self.files))

    def test_claim_collision_rolls_back_other_files(self):
        self.assertTrue(self.db.claim_group_upload(["existing.mp4"]))
        self.assertFalse(self.db.claim_group_upload(["new.mp4", "existing.mp4"]))
        self.assertTrue(self.db.claim_group_upload(["new.mp4"]))

    def test_success_is_recorded_and_not_sent_again(self):
        expected = {path for path, _ in self.files}

        self.assertEqual(self.worker.upload_group(self.files), expected)
        self.assertEqual(self.worker.upload_group(self.files), expected)

        self.post.assert_called_once()
        self.assertEqual(
            self.db.get_uploaded_files(), {Path(path).name for path in expected}
        )
        self.assertFalse(any(Path(path).exists() for path in expected))

    def test_only_one_concurrent_claim_succeeds(self):
        filenames = [Path(path).name for path, _ in self.files]

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.db.claim_group_upload, [filenames, filenames]))

        self.assertCountEqual(results, [True, False])

    def test_existing_database_is_upgraded_without_losing_uploads(self):
        self.db.mark_uploaded("old.mp4", 42)
        self.db.conn.execute("DROP TABLE group_upload_attempts")
        self.db.conn.commit()

        upgraded = Database()
        self.addCleanup(upgraded.conn.close)

        self.assertEqual(upgraded.get_uploaded_files(), {"old.mp4"})
        self.assertTrue(upgraded.claim_group_upload(["new.mp4"]))

    def test_database_failure_after_send_cannot_cause_resend(self):
        with patch.object(
            self.db, "mark_uploaded", side_effect=sqlite3.OperationalError("disk full")
        ):
            self.assertFalse(self.worker.upload_group(self.files))

        self.assertFalse(self.worker.upload_group(self.files))

        self.post.assert_called_once()
        self.assertFalse(self.db.get_uploaded_files())
        self.assertTrue(all(Path(path).exists() for path, _ in self.files))

    def test_already_uploaded_files_are_filtered_from_batch(self):
        first_path = self.files[0][0]
        self.db.mark_uploaded(Path(first_path).name, 100)

        self.assertEqual(
            self.worker.upload_group(self.files), {path for path, _ in self.files}
        )

        self.post.assert_called_once()
        self.assertNotIn(first_path, self.post.call_args.kwargs["data"]["media"])

    def test_single_video_uses_send_video_and_is_recorded(self):
        path, caption = self.files[0]
        self.response.json.return_value = {"result": {"message_id": 42}}

        self.assertEqual(self.worker.upload_group([(path, caption)]), {path})
        self.assertEqual(self.worker.upload_group([(path, caption)]), {path})

        self.post.assert_called_once()
        self.assertTrue(self.post.call_args.kwargs["url"].endswith("/sendVideo"))
        self.assertEqual(self.post.call_args.kwargs["data"]["caption"], caption)
        self.assertTrue(self.db.is_uploaded(Path(path).name))
        self.assertFalse(Path(path).exists())

    def test_single_document_uses_send_document(self):
        path, caption = self.files[0]
        self.response.json.return_value = {"result": {"message_id": 42}}

        with patch.object(Config, "TELEGRAM_UPLOAD_MODE", "document"):
            self.assertEqual(self.worker.upload_group([(path, caption)]), {path})

        self.post.assert_called_once()
        self.assertTrue(self.post.call_args.args[0].endswith("/sendDocument"))
        self.assertEqual(
            self.post.call_args.kwargs["data"]["document"], f"file://{path}"
        )

    def test_single_video_preserves_requested_chat(self):
        self.response.json.return_value = {"result": {"message_id": 42}}

        result = self.sender.upload_media_group(self.files[:1], "other-chat", "video")

        self.assertEqual(result, [{"message_id": 42}])
        self.assertEqual(self.post.call_args.kwargs["data"]["chat_id"], "other-chat")

    def test_batch_filtered_to_one_file_uses_send_video(self):
        first_path = self.files[0][0]
        self.db.mark_uploaded(Path(first_path).name, 100)
        self.response.json.return_value = {"result": {"message_id": 42}}

        self.assertEqual(
            self.worker.upload_group(self.files[:2]),
            {path for path, _ in self.files[:2]},
        )

        self.post.assert_called_once()
        self.assertEqual(
            self.post.call_args.kwargs["data"]["video"], f"file://{self.files[1][0]}"
        )

    def test_single_video_timeout_is_not_retried_or_sent_as_document(self):
        self.post.side_effect = [requests.ReadTimeout(), self.response]

        self.assertFalse(self.worker.upload_group(self.files[:1]))
        self.assertFalse(self.worker.upload_group(self.files[:1]))

        self.post.assert_called_once()
        self.sleep.assert_not_called()
        self.assertTrue(Path(self.files[0][0]).exists())
        self.assertFalse(self.db.get_uploaded_files())

    def test_low_disk_flush_splits_into_ten_ten_and_single_video(self):
        for i in range(10, 21):
            path = self.root / f"session_{i}.mp4"
            path.write_bytes(b"video")
            self.files.append((str(path), "caption"))
        batch_sizes = []

        def accept_valid_batch(*args, **kwargs):
            url = kwargs.get("url") or args[0]
            if url.endswith("/sendMediaGroup"):
                media = json.loads(kwargs["data"]["media"])
                self.assertTrue(2 <= len(media) <= 10)
                batch_sizes.append(len(media))
                result = [{"message_id": i + 1} for i in range(len(media))]
            else:
                self.assertTrue(url.endswith("/sendVideo"))
                batch_sizes.append(1)
                result = {"message_id": 21}
            return Mock(json=Mock(return_value={"result": result}))

        self.post.side_effect = accept_valid_batch
        self.recorder.check_disk_space.side_effect = RuntimeError("low disk")
        group = self.files.copy()
        uploaded = set()

        self.recorder._flush_group_if_needed(group, uploaded)

        self.assertEqual(batch_sizes, [10, 10, 1])
        self.assertFalse(group)
        self.assertEqual(uploaded, {path for path, _ in self.files})
        self.assertEqual(len(self.db.get_uploaded_files()), 21)

    def test_low_disk_flush_stops_on_failed_batch(self):
        self.recorder.check_disk_space.side_effect = RuntimeError("low disk")
        self.recorder._upload_group_batch = Mock(
            side_effect=[set(), AssertionError("batch retried")]
        )
        group = self.files.copy()

        self.recorder._flush_group_if_needed(group, set())

        self.recorder._upload_group_batch.assert_called_once()
        self.assertEqual(group, self.files)

    def test_normal_disk_keeps_incomplete_batch(self):
        group = self.files[:1]

        self.recorder._flush_group_if_needed(group, set())

        self.post.assert_not_called()
        self.assertEqual(group, self.files[:1])

    def test_invalid_batch_sizes_are_rejected_before_sending(self):
        for files in ([], self.files + self.files[:1]):
            with self.subTest(size=len(files)):
                with self.assertRaises(ValueError):
                    self.sender.upload_media_group(files, "test-channel", "video")

        self.post.assert_not_called()

    def test_restart_keeps_numbering_after_uploaded_files_are_deleted(self):
        self.recorder.current_session = "session"
        self.recorder._launch_processes = Mock()
        for path, _ in self.files:
            self.db.mark_uploaded(Path(path).name, 42)
            Path(path).unlink()

        self.recorder.restart_recording("stream-url", "title")

        self.assertEqual(self.recorder._launch_processes.call_args.args[2], 10)
        self.db.mark_uploaded("session_10.mp4", 43)

        self.recorder.restart_recording("stream-url", "title")

        self.assertEqual(self.recorder._launch_processes.call_args.args[2], 11)

    def test_restart_uses_highest_local_or_uploaded_number(self):
        self.recorder.current_session = "session"
        self.recorder._launch_processes = Mock()
        self.db.mark_uploaded("session_12.mp4", 42)

        self.recorder.restart_recording("stream-url", "title")

        self.assertEqual(self.recorder._launch_processes.call_args.args[2], 13)
        (self.root / "session_15.mp4").write_bytes(b"video")

        self.recorder.restart_recording("stream-url", "title")

        self.assertEqual(self.recorder._launch_processes.call_args.args[2], 16)

    def test_restart_ignores_other_sessions_and_nonsegment_names(self):
        self.recorder.current_session = "session"
        self.recorder._launch_processes = Mock()
        for filename in (
            "other_999.mp4",
            "session_other_999.mp4",
            "session_999.jpg",
        ):
            self.db.mark_uploaded(filename, 42)

        self.recorder.restart_recording("stream-url", "title")

        self.assertEqual(self.recorder._launch_processes.call_args.args[2], 10)

    def test_restart_without_segments_starts_at_one(self):
        self.recorder.current_session = "new_session"
        self.recorder._launch_processes = Mock()
        self.db.mark_uploaded("session_999.mp4", 42)

        self.recorder.restart_recording("stream-url", "title")

        self.recorder._launch_processes.assert_called_once_with(
            "stream-url", str(self.root / "new_session_%d.mp4"), 1
        )

    def test_stop_retains_watcher_that_outlives_join(self):
        watcher = Mock()
        watcher.is_alive.return_value = True
        self.recorder._watcher_thread = watcher
        self.recorder.running = True

        self.recorder.stop_recording()

        self.assertIs(self.recorder._watcher_thread, watcher)
        self.assertFalse(self.recorder.running)
        watcher.join.assert_called_once_with(timeout=60)

    def test_start_waits_without_changing_previous_session(self):
        watcher = Mock()
        watcher.is_alive.return_value = True
        self.recorder._watcher_thread = watcher
        self.recorder.current_session = "old_session"
        self.recorder.current_title = "old title"
        self.recorder.started_at = "2026-10-30T10:00:00+00:00"
        self.recorder._launch_processes = Mock()
        self.recorder._start_watcher_thread = Mock()

        with patch.object(self.worker, "reset_thread_anchor") as reset_anchor:
            with self.assertRaises(RuntimeError):
                self.recorder.start_recording(
                    "stream-url", "new title", "2026-10-30T11:00:00+00:00", "channel"
                )
            reset_anchor.assert_not_called()

        self.assertEqual(self.recorder.current_session, "old_session")
        self.assertEqual(self.recorder.current_title, "old title")
        self.assertEqual(self.recorder.started_at, "2026-10-30T10:00:00+00:00")
        self.recorder._launch_processes.assert_not_called()
        self.recorder._start_watcher_thread.assert_not_called()
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM streams").fetchone()[0], 0
        )

    def test_stop_clears_watcher_after_it_exits(self):
        watcher = Mock()
        watcher.is_alive.side_effect = [True, False, False]
        self.recorder._watcher_thread = watcher

        self.recorder.stop_recording()

        self.assertIsNone(self.recorder._watcher_thread)
        watcher.join.assert_called_once_with(timeout=60)

    def test_new_recording_starts_only_after_background_finalization_exits(self):
        entered = threading.Event()
        release = threading.Event()
        observed_titles = []
        self.recorder.current_session = "old_session"
        self.recorder.current_title = "old title"
        self.recorder.running = True
        self.recorder._launch_processes = Mock()
        self.recorder._start_watcher_thread = Mock()
        self.recorder._collect_new_segments_for_group = Mock()

        def slow_upload(*args):
            entered.set()
            release.wait(timeout=5)

        def finalize(*args):
            observed_titles.append(self.recorder.current_title)
            self.recorder._stop_pipeline()

        self.recorder._flush_group_if_needed = Mock(side_effect=slow_upload)
        self.recorder._finalize_stream = Mock(side_effect=finalize)
        watcher = threading.Thread(
            target=self.recorder._group_watcher,
            args=("old_session", set()),
            daemon=True,
        )
        self.recorder._watcher_thread = watcher
        watcher.start()
        try:
            self.assertTrue(entered.wait(timeout=5))
            with patch.object(watcher, "join"):
                self.recorder.stop_recording()
            with self.assertRaises(RuntimeError):
                self.recorder.start_recording(
                    "stream-url", "new title", "2026-10-30T11:00:00+00:00", "channel"
                )
            self.recorder._launch_processes.assert_not_called()
        finally:
            self.recorder.running = False
            release.set()
            watcher.join(timeout=5)

        self.assertFalse(watcher.is_alive())
        self.assertEqual(observed_titles, ["old title"])
        self.recorder._stop_pipeline.assert_called_once()

        self.recorder.start_recording(
            "stream-url", "new title", "2026-10-30T11:00:00+00:00", "channel"
        )

        self.assertEqual(self.recorder.current_title, "new title")
        self.recorder._launch_processes.assert_called_once()
        self.recorder._start_watcher_thread.assert_called_once()
        self.recorder._stop_pipeline.assert_called_once()


if __name__ == "__main__":
    unittest.main()
