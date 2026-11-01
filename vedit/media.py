"""ffmpeg / ffprobe の薄いラッパー."""
from __future__ import annotations

import json
import logging
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("vedit")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
REQUIRED_FILTERS = ["ass", "zoompan", "sidechaincompress", "silencedetect", "overlay", "crop"]


class RenderError(RuntimeError):
    pass


def require_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise RenderError(f"{tool} が見つかりません。README のセットアップ手順を参照してください。")
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    names = {line.split()[1] for line in out.splitlines() if len(line.split()) > 2}
    missing = [f for f in REQUIRED_FILTERS if f not in names]
    if missing:
        raise RenderError(f"ffmpeg に必要なフィルタがありません: {missing} (libass 付きビルドが必要)")


def run(cmd: list[str], desc: str, cwd: Path | None = None) -> str:
    log.debug("$ %s", shlex.join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-25:])
        raise RenderError(f"{desc} に失敗しました (exit {proc.returncode})\n$ {shlex.join(cmd)}\n{tail}")
    return proc.stderr


@dataclass
class MediaInfo:
    path: Path
    kind: str  # "video" | "image" | "audio" | "color"
    duration: float | None
    has_video: bool
    has_audio: bool
    width: int | None = None
    height: int | None = None
    color: str | None = None


def is_color_source(src: str) -> bool:
    return isinstance(src, str) and src.startswith("color:")


def probe(src: str | Path) -> MediaInfo:
    src_s = str(src)
    if is_color_source(src_s):
        return MediaInfo(Path(src_s), "color", None, True, False, color=src_s.split(":", 1)[1])
    path = Path(src_s)
    if not path.exists():
        raise RenderError(f"素材ファイルが見つかりません: {path}")
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RenderError(f"ffprobe に失敗: {path}\n{proc.stderr}")
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = data.get("format", {}).get("duration")
    dur = float(dur) if dur not in (None, "N/A") else None
    if path.suffix.lower() in IMAGE_EXTS:
        kind = "image"
    elif v is not None and not (v.get("disposition", {}).get("attached_pic")):
        kind = "video"
    elif a is not None:
        kind = "audio"
    else:
        raise RenderError(f"未対応の素材です: {path}")
    return MediaInfo(
        path=path.resolve(), kind=kind, duration=dur,
        has_video=kind in ("video", "image"), has_audio=a is not None and kind != "image",
        width=v.get("width") if v else None, height=v.get("height") if v else None,
    )


def duration_of(path: Path) -> float:
    info = probe(path)
    if info.duration is None:
        raise RenderError(f"長さを取得できません: {path}")
    return info.duration


_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silence(path: Path, noise: str, min_silence: float,
                   start: float = 0.0, length: float | None = None) -> list[tuple[float, float]]:
    """silencedetect で無音区間 [(start, end), ...] を返す (start 基準の相対秒)."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}"]
    if length is not None:
        cmd += ["-t", f"{length:.3f}"]
    cmd += ["-i", str(path), "-vn", "-af", f"silencedetect=noise={noise}:d={min_silence}", "-f", "null", "-"]
    err = run(cmd, f"無音検出 ({path.name})")
    silences: list[tuple[float, float]] = []
    cur = None
    for line in err.splitlines():
        if m := _SIL_START.search(line):
            cur = max(0.0, float(m.group(1)))
        elif (m := _SIL_END.search(line)) and cur is not None:
            silences.append((cur, float(m.group(1))))
            cur = None
    if cur is not None:  # 末尾まで無音
        silences.append((cur, length if length is not None else duration_of(path)))
    return silences
