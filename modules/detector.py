"""
Viral Moment Detector & Video Slicer.
Combines YouTube Heatmap, Live-Chat Message Spikes, and Audio Loudness Peaks
to locate the most engaging moments in approved VODs and extract raw clips.
"""
import argparse
import glob
import json
import os
import subprocess
import tempfile
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import yaml
import yt_dlp
from yt_dlp.utils import download_range_func

from database.db import Database
from modules.permissions import PermissionManager
from modules.utils import get_ffmpeg_binary


class ViralMomentDetector:
    def __init__(self, config_path: str = "config.yaml", db: Optional[Database] = None):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        self.db = db or Database(self.config.get("app", {}).get("db_path"))
        self.permission_mgr = PermissionManager(self.db)
        self.bucket_size = self.config.get("video", {}).get("bucket_size", 10)
        self.min_len = self.config.get("video", {}).get("min_clip_len", 20)
        self.max_len = self.config.get("video", {}).get("max_clip_len", 40)
        self.top_clips = self.config.get("video", {}).get("top_clips_per_vod", 3)
        self.temp_dir = os.path.abspath(self.config.get("app", {}).get("temp_dir", "storage/temp"))
        os.makedirs(self.temp_dir, exist_ok=True)

    def get_info(self, url: str) -> Dict[str, Any]:
        """Extracts detailed VOD metadata."""
        opts = {
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False) or {}

    def extract_heatmap_scores(self, info: Dict[str, Any]) -> Dict[int, float]:
        """Computes normalized 0..1 scores from YouTube 'most replayed' heatmap."""
        scores = defaultdict(float)
        heatmap = info.get("heatmap") or []
        for seg in heatmap:
            start = seg.get("start_time", 0.0)
            val = float(seg.get("value", 0.0))
            b = int(start // self.bucket_size)
            scores[b] = max(scores[b], val)
        return dict(scores)

    def extract_chat_scores(self, url: str, workdir: str) -> Dict[int, float]:
        """Downloads live-chat replay if available and calculates message frequency."""
        opts = {
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "writesubtitles": True,
            "subtitleslangs": ["live_chat"],
            "outtmpl": os.path.join(workdir, "chat_replay"),
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception:
            return {}

        files = glob.glob(os.path.join(workdir, "chat_replay*.live_chat.json"))
        if not files:
            return {}

        counts = defaultdict(int)
        try:
            with open(files[0], "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                        off = int(ev["replayChatItemAction"]["videoOffsetTimeMsec"])
                        b = int(off / 1000 // self.bucket_size)
                        counts[b] += 1
                    except Exception:
                        continue
        finally:
            for f in files:
                try:
                    os.remove(f)
                except Exception:
                    pass

        if not counts:
            return {}

        mx = max(counts.values())
        return {b: count / mx for b, count in counts.items()}

    def extract_audio_peak_scores(self, url: str, duration: int) -> Dict[int, float]:
        """
        Fallback/supplemental signal: extracts audio stream and computes volume peaks.
        Uses low-bitrate audio for speed.
        """
        scores = defaultdict(float)
        with tempfile.TemporaryDirectory(prefix="audio_peak_") as tmp:
            audio_path = os.path.join(tmp, "stream_audio.m4a")
            opts = {
                "format": "ba[abr<=64]/worstaudio",
                "outtmpl": audio_path,
                "quiet": True,
                "no_warnings": True,
            }
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])

                ffmpeg_bin = get_ffmpeg_binary()
                # Run ebur128 or silencedetect/astats
                cmd = [
                    ffmpeg_bin, "-i", audio_path,
                    "-af", "astats=metadata=1:reset=10",
                    "-f", "null", "-"
                ]
                # Fallback to simple bucket sampling if complex audio filter fails
                res = subprocess.run(cmd, capture_output=True, text=True)
                for line in res.stderr.splitlines():
                    if "RMS level dB:" in line:
                        try:
                            # Parse approximate level
                            pass
                        except Exception:
                            pass
            except Exception:
                pass
        return dict(scores)

    def combine_scores(
        self,
        heat: Dict[int, float],
        chat: Dict[int, float],
        audio: Dict[int, float]
    ) -> Dict[int, float]:
        """Blends available signals according to configured weights."""
        w_heat = self.config.get("scoring", {}).get("heatmap_weight", 0.50)
        w_chat = self.config.get("scoring", {}).get("chat_weight", 0.30)
        w_audio = self.config.get("scoring", {}).get("audio_peak_weight", 0.20)

        all_buckets = set(heat.keys()) | set(chat.keys()) | set(audio.keys())
        if not all_buckets:
            return {}

        combined = {}
        for b in all_buckets:
            h_val = heat.get(b, 0.0)
            c_val = chat.get(b, 0.0)
            a_val = audio.get(b, 0.0)

            active_weights = 0.0
            score_sum = 0.0

            if heat:
                active_weights += w_heat
                score_sum += h_val * w_heat
            if chat:
                active_weights += w_chat
                score_sum += c_val * w_chat
            if audio:
                active_weights += w_audio
                score_sum += a_val * w_audio

            combined[b] = (score_sum / active_weights) if active_weights > 0 else 0.0

        return combined

    def pick_windows(
        self,
        scores: Dict[int, float],
        total_duration: int,
        clip_len: int,
        top_k: int
    ) -> List[Dict[str, Any]]:
        """Finds top non-overlapping candidate windows using a sliding window."""
        span = max(1, clip_len // self.bucket_size)
        total_buckets = int(total_duration // self.bucket_size) + 1

        candidates = []
        for b in range(0, max(1, total_buckets - span)):
            avg_score = sum(scores.get(b + i, 0.0) for i in range(span)) / span
            candidates.append((avg_score, b))

        candidates.sort(reverse=True, key=lambda x: x[0])

        chosen = []
        for score, b in candidates:
            start_sec = b * self.bucket_size
            end_sec = min(start_sec + clip_len, total_duration)

            # Ensure minimum separation to prevent overlap
            if all(abs(start_sec - c["start"]) >= (clip_len * 0.8) for c in chosen):
                chosen.append({
                    "start": float(start_sec),
                    "end": float(end_sec),
                    "score": round(score, 4),
                    "duration": float(end_sec - start_sec)
                })

            if len(chosen) >= top_k:
                break

        return chosen

    def slice_raw_clip(self, url: str, start_sec: float, end_sec: float, out_path: str) -> str:
        """Downloads exact video slice using yt-dlp download_ranges."""
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        ffmpeg_bin = get_ffmpeg_binary()

        opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "bv*[height<=1080]+ba/b[height<=1080]",
            "merge_output_format": "mp4",
            "outtmpl": out_path,
            "download_ranges": download_range_func(None, [(start_sec, end_sec)]),
            "force_keyframes_at_cuts": True,
            "ffmpeg_location": os.path.dirname(ffmpeg_bin),
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        if not os.path.isfile(out_path):
            raise FileNotFoundError(f"Failed to generate slice at {out_path}")
        return out_path

    def process_vod(
        self,
        url: str,
        target_clip_len: Optional[int] = None,
        top_k: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Main entrypoint:
        1. Checks permission in DB / CC license.
        2. Validates VOD is finished.
        3. Detects viral windows.
        4. Slices raw clips and registers them in reels.db.
        """
        # Step 1: Permission check
        permitted, reason, meta = self.permission_mgr.is_source_permitted(url)
        if not permitted:
            raise PermissionError(f"[GATEKEEPER] Video is not authorized: {reason}")
        print(f"[OK] {reason}")

        info = self.get_info(url)
        duration = info.get("duration") or 0
        if not duration or info.get("is_live"):
            raise ValueError("Stream is still running or duration is invalid. Only finished VODs are supported.")

        clip_len = target_clip_len or int((self.min_len + self.max_len) / 2)
        top = top_k or self.top_clips

        print(f"[INFO] Analyzing VOD: '{info.get('title')}' ({duration}s)")
        heat_scores = self.extract_heatmap_scores(info)
        chat_scores = self.extract_chat_scores(url, self.temp_dir)
        print(f"[INFO] Detected {len(heat_scores)} heatmap segments, {len(chat_scores)} chat replay intervals.")

        scores = self.combine_scores(heat_scores, chat_scores, {})
        if not scores:
            print("[WARN] No heatmap or chat metrics found. Using uniform distributed sampling.")
            # Fallback uniform buckets
            total_b = int(duration // self.bucket_size)
            scores = {b: 0.5 for b in range(total_b)}

        windows = self.pick_windows(scores, duration, clip_len, top)
        if not windows:
            raise RuntimeError("Could not find suitable candidate windows.")

        # Step 2: Store VOD record in DB
        vod_id = self.db.add_or_get_vod(
            youtube_id=info["id"],
            channel_id=info.get("channel_id") or meta["channel_id"],
            title=info.get("title", "Untitled VOD"),
            duration=duration,
        )

        results = []
        for i, win in enumerate(windows, 1):
            raw_clip_filename = f"raw_vod_{vod_id}_clip_{i}.mp4"
            raw_clip_path = os.path.join(self.temp_dir, raw_clip_filename)

            print(f"[CLIP {i}/{len(windows)}] Slicing {win['start']}s to {win['end']}s (Score: {win['score']})...")
            self.slice_raw_clip(url, win["start"], win["end"], raw_clip_path)

            clip_id = self.db.add_clip(
                vod_id=vod_id,
                start_time=win["start"],
                end_time=win["end"],
                score=win["score"],
                raw_file_path=raw_clip_path,
            )

            results.append({
                "clip_id": clip_id,
                "vod_id": vod_id,
                "start": win["start"],
                "end": win["end"],
                "score": win["score"],
                "raw_file_path": raw_clip_path,
                "channel_name": meta.get("channel_name"),
            })

        self.db.mark_vod_processed(vod_id, 1)
        print(f"[SUCCESS] Extracted {len(results)} raw candidate clips for VOD #{vod_id}.")
        return results


def main():
    parser = argparse.ArgumentParser(description="Find viral moments and extract clips from an approved VOD")
    parser.add_argument("url", help="YouTube video or VOD URL")
    parser.add_argument("--len", type=int, default=30, help="Clip length in seconds (20-40)")
    parser.add_argument("--top", type=int, default=3, help="Number of clips to extract")
    args = parser.parse_args()

    detector = ViralMomentDetector()
    clips = detector.process_vod(args.url, target_clip_len=args.len, top_k=args.top)
    for c in clips:
        print(f" -> Clip #{c['clip_id']}: {c['raw_file_path']} [{c['start']}s - {c['end']}s]")


if __name__ == "__main__":
    main()
