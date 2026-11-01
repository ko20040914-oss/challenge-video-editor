#!/usr/bin/env python3
"""テスト用ダミー素材を ffmpeg だけで生成する (外部ダウンロードなし・著作権フリー).

生成物 (既定: examples/assets/):
  talk.mp4        話者カメラ想定。テストパターン + 合成音声。文の間に長めの無音 (ジャンプカット検証用)
  broll.mp4       SMPTE カラーバー + 440Hz トーン (B ロール想定)
  wipe.mp4        ワイプ (PiP) 用の小さいテストパターン
  photo1.png      Ken Burns 用 4K 静止画 (マンデルブロ集合)
  photo2.png      パン用 4K 静止画 (グラデーション + グリッド)
  nar_*.wav       ナレーション (ffmpeg 内蔵 flite による英語 TTS。無ければトーンで代用)
  bgm.wav         BGM (sine 波で合成したコード進行 + キック、32 秒ループ)
  se_pop.wav / se_whoosh.wav / se_impact.wav  効果音

使い方:  python3 tools/make_dummy_assets.py [--out examples/assets]
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

FONT = "IPAGothic"


def ff(*args: str) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-nostdin", *args]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"失敗: {shlex.join(cmd)}\n{p.stderr}")


def has_flite() -> bool:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return any(line.split()[1:2] == ["flite"] for line in p.stdout.splitlines())


def speech(out: Path, parts: list[str | float], voice: str = "slt") -> None:
    """parts: 文字列 = 発話, 数値 = 無音秒. 48kHz mono WAV を出力."""
    tmp = out.parent / f".tmp_{out.stem}"
    tmp.mkdir(exist_ok=True)
    files = []
    use_flite = has_flite()
    for i, part in enumerate(parts):
        f = tmp / f"{i:02d}.wav"
        if isinstance(part, (int, float)):
            ff("-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono", "-t", str(part), f)
        elif use_flite:
            text = part.replace("'", "").replace(":", " ").replace(",", " ")
            ff("-f", "lavfi", "-i", f"flite=text='{text}':voice={voice}", "-af",
               "aresample=48000,volume=2.0", "-ac", "1", f)
        else:  # flite の無い ffmpeg 用: 語数ぶんの短いトーン列で「話している」状態を模擬
            words = len(part.split())
            expr = "0.4*sin(2*PI*(180+40*sin(2*PI*3*t))*t)*gt(sin(2*PI*2.5*t),-0.3)"
            ff("-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d={0.32 * words:.2f}", f)
        files.append(f)
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in files))
    ff("-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "48000", "-ac", "1", out)
    for p in tmp.iterdir():
        p.unlink()
    tmp.rmdir()


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True).stdout
    return float(out.strip())


def label(text: str, size: int = 64, y: str = "h-th-60") -> str:
    return (f"drawtext=font={FONT}:text='{text}':fontsize={size}:fontcolor=white:borderw=4:"
            f"bordercolor=black:x=(w-tw)/2:y={y}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "examples" / "assets"))
    out = Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"ダミー素材を生成: {out}")

    # --- ナレーション / トーク音声 (文の間に無音 = ジャンプカット対象)
    speech(out / "talk_audio.wav", [0.6, "Hello everyone.", 1.4,
                                    "Today I will show you an automatic video editor.", 1.6,
                                    "It removes the silent parts for you.", 1.2,
                                    "Pretty cool, right?", 0.8])
    speech(out / "nar_photo.wav", [0.2, "Zoom and pan bring still images to life."])
    speech(out / "nar_broll.wav", [0.2, "Picture in picture shows two things at once.", 1.5,
                                   "And the music ducks under my voice.", 0.4], voice="kal")
    speech(out / "nar_pan.wav", [0.2, "Everything is generated from a cut list."])
    speech(out / "nar_hook.wav", [0.1, "Ninety seconds can change the future."], voice="awb")

    # --- 話者カメラ: testsrc2 + ラベル。長さは音声に合わせる
    talk_d = duration(out / "talk_audio.wav")
    ff("-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=30:d={talk_d:.3f}", "-i", str(out / "talk_audio.wav"),
       "-vf", label("A-ROLL / TALK CAMERA", 72, "(h-th)/2") + "," + label("（無音区間あり→ジャンプカット）", 48, "(h/2)+70"),
       "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "160k", "-shortest", out / "talk.mp4")
    (out / "talk_audio.wav").unlink()

    # --- B ロール: カラーバー + 小さなトーン
    ff("-f", "lavfi", "-i", "smptehdbars=s=1920x1080:r=30:d=12", "-f", "lavfi",
       "-i", "sine=f=440:r=48000:d=12", "-vf", label("B-ROLL", 80, "60"), "-af", "volume=0.1",
       "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-shortest", out / "broll.mp4")

    # --- ワイプ (PiP) 用
    ff("-f", "lavfi", "-i", "testsrc=s=640x360:r=30:d=10", "-vf", label("WIPE", 56, "(h-th)/2"),
       "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-an", out / "wipe.mp4")

    # --- 静止画 (4K)
    ff("-f", "lavfi", "-i", "mandelbrot=s=3840x2160:maxiter=600:start_scale=2.4:end_scale=2.4",
       "-frames:v", "1", "-vf", label("PHOTO 1 (Ken Burns)", 120, "h-th-120"), out / "photo1.png")
    ff("-f", "lavfi", "-i", "gradients=s=3840x2160:c0=0x1d3557:c1=0xe63946:c2=0xf1faee:c3=0x457b9d:n=4:seed=7",
       "-frames:v", "1",
       "-vf", "drawgrid=w=240:h=240:t=3:c=white@0.35," + label("PHOTO 2 (Pan)", 160, "(h-th)/2"),
       out / "photo2.png")

    # --- BGM: C - Am - F - G (各 2 秒) のパッド + 8 分刻みのキック, 32 秒
    chord = ("st(0,mod(floor(t/2),4));"
             "st(1,if(eq(ld(0),0),261.63,if(eq(ld(0),1),220.00,if(eq(ld(0),2),174.61,196.00))));"
             "st(2,ld(1)*1.2599);st(3,ld(1)*1.4983);st(4,mod(t,0.5));"
             "0.09*(sin(2*PI*ld(1)*t)+0.8*sin(2*PI*ld(2)*t)+0.7*sin(2*PI*ld(3)*t)+0.5*sin(PI*ld(1)*t))"
             "*(0.75+0.25*sin(2*PI*0.5*t))"
             "+0.35*sin(2*PI*(50+120*exp(-30*ld(4)))*ld(4))*exp(-9*ld(4))"
             "+0.05*sin(2*PI*ld(1)*2*t)*exp(-6*mod(t,0.25))")
    ff("-f", "lavfi", "-i", f"aevalsrc='{chord}':s=48000:d=32", "-af",
       "aformat=channel_layouts=stereo,afade=t=in:d=0.02,volume=0.9", out / "bgm.wav")

    # --- 効果音
    ff("-f", "lavfi", "-i", "aevalsrc='0.8*sin(2*PI*(700*t+5000*t*t))*exp(-22*t)':s=48000:d=0.25",
       "-ac", "2", out / "se_pop.wav")
    ff("-f", "lavfi", "-i", "anoisesrc=d=0.6:c=pink:r=48000:a=0.8", "-af",
       "bandpass=f=1200:w=900,afade=t=in:d=0.3,afade=t=out:st=0.3:d=0.3,volume=2.5", "-ac", "2",
       out / "se_whoosh.wav")
    ff("-f", "lavfi", "-i",
       "aevalsrc='0.9*sin(2*PI*(45+90*exp(-12*t))*t)*exp(-5*t)+0.3*(random(0)*2-1)*exp(-30*t)':s=48000:d=0.9",
       "-ac", "2", out / "se_impact.wav")

    for p in sorted(out.iterdir()):
        print(f"  {p.name:16s} {p.stat().st_size / 1024:8.0f} KB" +
              (f"  {duration(p):5.2f}s" if p.suffix in (".mp4", ".wav") else ""))


if __name__ == "__main__":
    main()
