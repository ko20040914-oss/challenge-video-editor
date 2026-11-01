#!/usr/bin/env python3
"""BGM ダッキングの効き具合を測る (仕様: 会話時 0.12〜0.20倍 / 間 0.35〜0.45倍).

  python3 -m vedit render examples/sample_30s.json --keep-temp
  python3 tools/check_ducking.py examples/sample_30s.json

--keep-temp で残したカットごとのセグメント (声のトラック) と BGM を、カット表と同じ
sidechaincompress 設定で鳴らし、0.5 秒窓ごとに「ダッキング後 BGM / 元 BGM」の比を測る。
声のある窓 (RMS > 0.03) と無い窓に分けて、BGM の実効倍率 (= volume × 比) の平均を出す。
"""
from __future__ import annotations

import array
import glob
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vedit import presets as P  # noqa: E402
from vedit.cutlist import load_cutlist  # noqa: E402
from vedit.segment import resolve  # noqa: E402

AFMT = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
SR, WIN, VOICE_RMS = 8000, 4000, 0.03


def main() -> None:
    cutlist = Path(sys.argv[1])
    doc = load_cutlist(cutlist)
    pr = doc["project"]
    bgm, duck = pr["bgm"], pr["bgm"]["ducking"]
    out = Path(pr["output"])
    out = out if out.is_absolute() else (doc["_base_dir"] / out).resolve()
    segs = sorted(glob.glob(str(out.parent / f".work_{out.stem}" / "seg_*.mkv")))
    if not segs:
        sys.exit("セグメントがありません。先に --keep-temp 付きでレンダリングしてください")
    n = len(segs)
    inp = [a for s in segs for a in ("-i", s)] + ["-stream_loop", "-1", "-i", resolve(doc["_base_dir"], bgm["file"])]
    cat = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[acat];"

    def pcm(chain: str) -> array.array:
        cmd = ["ffmpeg", "-loglevel", "error", *inp, "-filter_complex", cat + chain, "-map", "[o]",
               "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
        return array.array("h", subprocess.run(cmd, capture_output=True, check=True).stdout)

    voice = pcm("[acat]anull[o]")
    total = len(voice) / SR
    b = f"[{n}:a]{AFMT},atrim=duration={total}"
    raw = pcm(f"{b}[o];[acat]anullsink")
    ducked = pcm(f"{b}[b];[acat]asplit[v][sc];[v]anullsink;[b][sc]sidechaincompress=threshold={duck['threshold']}"
                 f":ratio={duck['ratio']}:attack={duck['attack']}:release={duck['release']}:makeup={duck['makeup']}[o]")

    def rms(x, a, z):
        seg = x[a:z]
        return math.sqrt(sum(v * v for v in seg) / max(1, len(seg))) / 32768

    talk, gap = [], []
    for i in range(0, min(len(voice), len(raw), len(ducked)) - WIN, WIN):
        ratio = rms(ducked, i, i + WIN) / max(rms(raw, i, i + WIN), 1e-9)
        (talk if rms(voice, i, i + WIN) > VOICE_RMS else gap).append(float(bgm["volume"]) * ratio)
    lo, hi = P.BGM_TALK_RANGE
    t, g = sum(talk) / len(talk), sum(gap) / len(gap)
    print(f"設定: volume={bgm['volume']} threshold={duck['threshold']} ratio={duck['ratio']} "
          f"attack={duck['attack']}ms release={duck['release']}ms")
    print(f"会話中 ({len(talk)} 窓): BGM {t:.3f} 倍  (仕様 {lo}〜{hi}) {'OK' if lo <= t <= hi else 'NG'}")
    print(f"間     ({len(gap)} 窓): BGM {g:.3f} 倍  (仕様 0.35〜0.45) {'OK' if 0.35 <= g <= 0.45 else 'NG'}")


if __name__ == "__main__":
    main()
