"""
System and media utility functions.
Resolves FFmpeg/FFprobe binaries across Windows systems.
"""
import os
import shutil
from typing import Optional


def get_ffmpeg_binary() -> str:
    """Finds ffmpeg executable from PATH or bundled imageio-ffmpeg."""
    path = shutil.which("ffmpeg")
    if path:
        return path

    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass

    # Common Windows fallback paths
    common_paths = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        os.path.expanduser(r"~\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg\bin\ffmpeg.exe"),
    ]
    for p in common_paths:
        if os.path.isfile(p):
            return p

    raise RuntimeError("FFmpeg executable not found. Install imageio-ffmpeg or add ffmpeg to PATH.")


def get_ffprobe_binary() -> Optional[str]:
    """Finds ffprobe executable from PATH or adjacent to ffmpeg."""
    path = shutil.which("ffprobe")
    if path:
        return path

    try:
        ffmpeg_bin = get_ffmpeg_binary()
        ffprobe_cand = os.path.join(os.path.dirname(ffmpeg_bin), "ffprobe.exe" if os.name == "nt" else "ffprobe")
        if os.path.isfile(ffprobe_cand):
            return ffprobe_cand
    except Exception:
        pass

    return None
