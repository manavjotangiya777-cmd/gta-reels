#!/usr/bin/env python3
"""
GTA 6 news Reel renderer (free tools only: edge-tts + FFmpeg).

Usage:
  pip install edge-tts
  sudo apt install ffmpeg
  python render.py --script script.txt --images img1.jpg img2.jpg img3.jpg --out reel.mp4
  # optional: --music bg.mp3 --voice en-IN-PrabhatNeural

Output: 1080x1920 mp4, voiceover + burned-in captions, slow zoom on each image.
Note: edge-tts voices: hi-IN-MadhurNeural / hi-IN-SwaraNeural (Hindi),
      en-IN-PrabhatNeural / en-IN-NeerjaNeural (Indian English).
"""
import argparse
import os
import shutil
import subprocess
import tempfile

W, H, FPS = 1080, 1920, 30


def run(cmd, cwd=None):
    subprocess.run(cmd, check=True, cwd=cwd)


def duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path]
    )
    return float(out.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True, help="text file with the voiceover script")
    ap.add_argument("--images", nargs="+", required=True, help="background images (in order)")
    ap.add_argument("--out", default="reel.mp4")
    ap.add_argument("--voice", default="en-IN-PrabhatNeural")
    ap.add_argument("--music", default=None, help="optional background music file")
    args = ap.parse_args()

    out_path = os.path.abspath(args.out)
    work = tempfile.mkdtemp(prefix="reel_")
    try:
        # copy inputs into the work dir so ffmpeg filter paths stay simple
        shutil.copy(args.script, os.path.join(work, "script.txt"))
        imgs = []
        for i, p in enumerate(args.images):
            ext = os.path.splitext(p)[1] or ".jpg"
            name = f"img{i}{ext}"
            shutil.copy(p, os.path.join(work, name))
            imgs.append(name)
        music = None
        if args.music:
            shutil.copy(args.music, os.path.join(work, "music.mp3"))
            music = "music.mp3"

        # 1) voiceover + subtitles (free, via Edge TTS)
        run(["edge-tts", "--voice", args.voice, "--file", "script.txt",
             "--write-media", "voice.mp3", "--write-subtitles", "subs.vtt"], cwd=work)
        total = duration(os.path.join(work, "voice.mp3")) + 0.5
        per = total / len(imgs)
        frames = int(per * FPS)

        # 2) one zoom clip per image
        clips = []
        for i, img in enumerate(imgs):
            clip = f"clip{i}.mp4"
            vf = (
                f"scale={int(W*1.25)}:{int(H*1.25)}:force_original_aspect_ratio=increase,"
                f"crop={int(W*1.25)}:{int(H*1.25)},"
                f"zoompan=z='min(zoom+0.0012,1.25)':d={frames}:s={W}x{H}:fps={FPS},"
                "format=yuv420p"
            )
            run(["ffmpeg", "-y", "-loop", "1", "-i", img, "-vf", vf,
                 "-t", f"{per:.2f}", "-r", str(FPS), "-c:v", "libx264", clip], cwd=work)
            clips.append(clip)

        with open(os.path.join(work, "list.txt"), "w") as f:
            for c in clips:
                f.write(f"file '{c}'\n")
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt",
             "-c", "copy", "slideshow.mp4"], cwd=work)

        # 3) burn captions + mix audio
        style = ("FontSize=16,Bold=1,Alignment=2,MarginV=260,"
                 "Outline=3,PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&")
        sub_filter = f"subtitles=subs.vtt:force_style='{style}'"

        cmd = ["ffmpeg", "-y", "-i", "slideshow.mp4", "-i", "voice.mp3"]
        if music:
            cmd += ["-stream_loop", "-1", "-i", music]
            cmd += ["-filter_complex",
                    f"[0:v]{sub_filter}[v];"
                    "[2:a]volume=0.12[m];[1:a][m]amix=inputs=2:duration=first[a]",
                    "-map", "[v]", "-map", "[a]"]
        else:
            cmd += ["-vf", sub_filter, "-map", "0:v", "-map", "1:a"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-shortest", "-movflags", "+faststart", out_path]
        run(cmd, cwd=work)
        print(f"Done: {out_path}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
