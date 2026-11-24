#!/usr/bin/env python3
"""
Auto clipper (free tools: yt-dlp + FFmpeg).

Finds the most "viral" moments of a YouTube VOD / finished stream using:
  1. YouTube "Most replayed" heatmap   (yt-dlp metadata, free)
  2. Live-chat message-rate spikes     (chat replay, free)
Scores 10-second buckets, picks top non-overlapping windows, cuts them and
reframes to 9:16 (blurred background + centered video).

ONLY use on videos where the creator gave you permission (or CC-BY licensed
videos) and keep the credit in the caption. Keep proof of permission.

Usage:
  pip install -U yt-dlp
  sudo apt install ffmpeg
  python auto_clipper.py "https://www.youtube.com/watch?v=XXXX" \
      --top 5 --len 40 --credit "@CreatorName" --outdir clips

Output: clips/clip_1.mp4 ... and clips/clips.json (start, end, score, credit)
for the n8n workflow to read.
"""
import argparse
import glob
import json
import os
import subprocess
from collections import defaultdict

import yt_dlp
from yt_dlp.utils import download_range_func

BUCKET = 10  # seconds


def get_info(url):
    with yt_dlp.YoutubeDL({"skip_download": True, "quiet": True}) as ydl:
        return ydl.extract_info(url, download=False)


def heatmap_scores(info):
    """'Most replayed' data: list of {start_time, end_time, value 0..1}."""
    scores = defaultdict(float)
    for seg in info.get("heatmap") or []:
        b = int(seg["start_time"] // BUCKET)
        scores[b] = max(scores[b], float(seg["value"]))
    return scores


def chat_scores(url, workdir):
    """Chat replay message rate per bucket, normalised 0..1. Empty if no chat."""
    opts = {
        "skip_download": True, "quiet": True,
        "writesubtitles": True, "subtitleslangs": ["live_chat"],
        "outtmpl": os.path.join(workdir, "chat"),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:  # chat replay is optional
        print("No chat replay:", e)
        return {}
    files = glob.glob(os.path.join(workdir, "chat*.live_chat.json"))
    if not files:
        return {}
    counts = defaultdict(int)
    with open(files[0], encoding="utf-8") as f:
        for line in f:
            try:
                ev = json.loads(line)
                off = int(ev["replayChatItemAction"]["videoOffsetTimeMsec"])
                counts[int(off / 1000 // BUCKET)] += 1
            except Exception:
                continue
    if not counts:
        return {}
    mx = max(counts.values())
    return {b: c / mx for b, c in counts.items()}


def pick_windows(scores, duration, clip_len, top):
    """Sliding window over buckets, greedy non-overlapping top picks."""
    n = int(duration // BUCKET) + 1
    span = max(1, clip_len // BUCKET)
    cands = []
    for b in range(0, max(1, n - span)):
        s = sum(scores.get(b + i, 0) for i in range(span)) / span
        cands.append((s, b))
    cands.sort(reverse=True)
    chosen = []
    for s, b in cands:
        start = b * BUCKET
        if all(abs(start - c["start"]) >= clip_len for c in chosen):
            chosen.append({"start": start, "end": min(start + clip_len, int(duration)),
                           "score": round(s, 3)})
        if len(chosen) >= top:
            break
    return chosen


def cut_and_reframe(url, win, idx, outdir):
    raw = os.path.join(outdir, f"raw_{idx}.mp4")
    opts = {
        "quiet": True,
        "format": "bv*[height<=1080]+ba/b[height<=1080]",
        "merge_output_format": "mp4",
        "outtmpl": raw,
        "download_ranges": download_range_func(None, [(win["start"], win["end"])]),
        "force_keyframes_at_cuts": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    out = os.path.join(outdir, f"clip_{idx}.mp4")
    fc = (
        "[0:v]split[a][b];"
        "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg];"
        "[b]scale=1080:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", raw, "-filter_complex", fc,
         "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-c:a", "aac",
         "-movflags", "+faststart", out],
        check=True,
    )
    os.remove(raw)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--len", type=int, default=40, help="clip length in seconds")
    ap.add_argument("--credit", default="")
    ap.add_argument("--outdir", default="clips")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    info = get_info(args.url)
    duration = info.get("duration") or 0
    if not duration:
        raise SystemExit("Live stream still running or duration unknown. Use after it ends (VOD).")

    heat = heatmap_scores(info)
    chat = chat_scores(args.url, args.outdir)
    print(f"heatmap buckets: {len(heat)}, chat buckets: {len(chat)}")

    # combine: heatmap 60%, chat 40% (if only one exists it is used alone)
    scores = {}
    for b in set(heat) | set(chat):
        if heat and chat:
            scores[b] = 0.6 * heat.get(b, 0) + 0.4 * chat.get(b, 0)
        else:
            scores[b] = heat.get(b, 0) or chat.get(b, 0)
    if not scores:
        raise SystemExit("No heatmap or chat data. Add audio-peak / Whisper scoring as a fallback.")

    wins = pick_windows(scores, duration, args.len, args.top)
    results = []
    for i, w in enumerate(wins, 1):
        path = cut_and_reframe(args.url, w, i, args.outdir)
        results.append({**w, "file": path, "source": args.url,
                        "title": info.get("title"), "credit": args.credit})
        print("created", path, w)

    with open(os.path.join(args.outdir, "clips.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
