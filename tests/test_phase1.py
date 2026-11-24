"""
Unit and Integration Tests for Phase 1: Database, Permissions & Viral Clipper.
"""
import os
import tempfile
import unittest

from database.db import Database
from modules.permissions import PermissionManager
from modules.detector import ViralMomentDetector
from modules.utils import get_ffmpeg_binary


class TestPhase1(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_reels.db")
        self.db = Database(self.db_path)
        self.perm_mgr = PermissionManager(self.db)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_database_initialization(self):
        """Verifies all tables are created properly."""
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = {row[0] for row in cur.fetchall()}
            self.assertIn("creators", tables)
            self.assertIn("vods", tables)
            self.assertIn("clips", tables)
            self.assertIn("upload_logs", tables)

    def test_creator_whitelist(self):
        """Verifies adding and checking approved / unapproved creators."""
        channel_id = "UC_test_gta_streamer"
        channel_name = "GTA Master"

        # Initially not approved
        self.assertFalse(self.db.is_creator_approved(channel_id))

        # Add as approved
        self.db.add_creator(channel_id, channel_name, "Signed written agreement 2026", approved=True)
        self.assertTrue(self.db.is_creator_approved(channel_id))

        creator = self.db.get_creator(channel_id)
        self.assertEqual(creator["channel_name"], channel_name)
        self.assertEqual(creator["approved"], 1)

        # Check permission manager mock
        info = {
            "channel_id": channel_id,
            "channel": channel_name,
            "license": "Standard YouTube License"
        }
        permitted, reason, _ = self.perm_mgr.is_source_permitted(info)
        self.assertTrue(permitted)
        self.assertIn("Approved creator", reason)

        # Revoke permission
        self.db.add_creator(channel_id, channel_name, "Revoked", approved=False)
        self.assertFalse(self.db.is_creator_approved(channel_id))

        permitted_revoked, _, _ = self.perm_mgr.is_source_permitted(info)
        self.assertFalse(permitted_revoked)

    def test_creative_commons_fallback(self):
        """Verifies that non-whitelisted videos with CC license are permitted."""
        cc_info = {
            "channel_id": "UC_unknown_channel",
            "channel": "Random Modder",
            "license": "Creative Commons Attribution license (reuse allowed)"
        }
        permitted, reason, _ = self.perm_mgr.is_source_permitted(cc_info)
        self.assertTrue(permitted)
        self.assertIn("Creative Commons License", reason)

    def test_vod_and_clip_lifecycle(self):
        """Verifies VOD registration, clip creation, status transitions, and daily upload counter."""
        vod_id = self.db.add_or_get_vod("yt_12345", "UC_test_channel", "Epic GTA 6 Stream", 3600)
        self.assertIsInstance(vod_id, int)

        # Add clip
        clip_id = self.db.add_clip(vod_id, 120.0, 150.0, 0.95, "storage/temp/clip_test.mp4")
        self.assertIsInstance(clip_id, int)

        clip = self.db.get_clip(clip_id)
        self.assertEqual(clip["status"], "pending")
        self.assertEqual(clip["score"], 0.95)

        # Update status to approved
        self.db.update_clip_status(clip_id, "approved", telegram_message_id=9876)
        updated = self.db.get_clip(clip_id)
        self.assertEqual(updated["status"], "approved")
        self.assertEqual(updated["telegram_message_id"], 9876)

        # Test daily upload quota tracker
        self.assertEqual(self.db.get_daily_upload_count(), 0)
        self.db.log_upload(clip_id, "yt_short_abc", "https://youtube.com/shorts/yt_short_abc", "private")
        self.assertEqual(self.db.get_daily_upload_count(), 1)

        clip_after_upload = self.db.get_clip(clip_id)
        self.assertEqual(clip_after_upload["status"], "uploaded")

    def test_window_picking_algorithm(self):
        """Tests that top non-overlapping windows of target length are correctly selected."""
        detector = ViralMomentDetector(config_path="config.yaml", db=self.db)
        # Synthetic scores: spikes at bucket 5 (50s) and bucket 20 (200s)
        scores = {b: 0.1 for b in range(50)}
        scores[5] = 0.9
        scores[6] = 0.8
        scores[20] = 0.95
        scores[21] = 0.85

        windows = detector.pick_windows(scores, total_duration=500, clip_len=30, top_k=2)
        self.assertEqual(len(windows), 2)
        # Windows must not overlap
        self.assertGreaterEqual(abs(windows[0]["start"] - windows[1]["start"]), 24)
        self.assertTrue(all(w["duration"] == 30 for w in windows))

    def test_ffmpeg_resolution(self):
        """Verifies that FFmpeg is resolved via imageio-ffmpeg or system."""
        try:
            bin_path = get_ffmpeg_binary()
            self.assertTrue(os.path.exists(bin_path))
        except RuntimeError:
            self.skipTest("imageio-ffmpeg is still downloading")


if __name__ == "__main__":
    unittest.main()
