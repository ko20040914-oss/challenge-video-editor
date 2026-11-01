"""全体のレンダリング: カット計画 → セグメント生成 → 結合 + テロップ焼き込み + BGM ダッキング + SE."""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

from . import presets as P
from .cutlist import load_cutlist
from .media import RenderError, duration_of, probe, require_tools, run
from .segment import plan_cut, render_segment, resolve
from .telop import AssBuilder

log = logging.getLogger("vedit")
AFMT = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


def _filter_quote(path: str) -> str:
    return "'" + path.replace("\\", "/").replace("'", r"'\''") + "'"


def build_audio_graph(n_seg: int, total: float, project: dict[str, Any], se_events: list[dict[str, Any]],
                      first_extra_input: int, loudnorm: str | None,
                      limiter: bool = True) -> tuple[list[list[str]], list[str]]:
    """結合済み音声 [acat] に BGM(ダッキング) と SE を重ねるグラフ. 戻り値: (追加入力, chains)."""
    inputs: list[list[str]] = []
    chains = [f"{''.join(f'[{i}:a]' for i in range(n_seg))}concat=n={n_seg}:v=0:a=1[acat]"]
    idx = first_extra_input
    mix = []
    bgm = project.get("bgm")
    base = Path(project["_base_dir"])
    if bgm and bgm.get("file"):
        inputs.append(["-stream_loop", "-1", "-i", resolve(base, bgm["file"])])
        fo = float(bgm["fade_out"])
        chains.append(f"[{idx}:a]{AFMT},atrim=duration={total:.3f},asetpts=PTS-STARTPTS,"
                      f"volume={float(bgm['volume'])},afade=t=in:d={float(bgm['fade_in'])},"
                      f"afade=t=out:st={max(0.0, total - fo):.3f}:d={fo}[bgm]")
        idx += 1
        duck = bgm.get("ducking", {})
        if duck.get("enabled", True):
            chains.append("[acat]asplit=2[voice][sc]")
            chains.append(f"[bgm][sc]sidechaincompress=threshold={duck['threshold']}:ratio={duck['ratio']}"
                          f":attack={duck['attack']}:release={duck['release']}:makeup={duck['makeup']}[bgmd]")
            mix += ["[voice]", "[bgmd]"]
        else:
            mix += ["[acat]", "[bgm]"]
    else:
        mix.append("[acat]")
    for j, se in enumerate(se_events):
        inputs.append(["-i", se["path"]])
        ms = int(round(se["time"] * 1000))
        chains.append(f"[{idx}:a]{AFMT},volume={float(se.get('volume', 1.0))},adelay={ms}:all=1[se{j}]")
        mix.append(f"[se{j}]")
        idx += 1
    post = [f"apad", f"atrim=duration={total:.3f}"]
    if loudnorm:
        post += [loudnorm, "aresample=48000"]
    if limiter:
        post.append("alimiter=limit=0.95:level=disabled")
    if len(mix) > 1:
        chains.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first,{','.join(post)}[aout]")
    else:
        chains.append(f"{mix[0]}{','.join(post)}[aout]")
    return inputs, chains


def measure_loudness(seg_inputs: list[list[str]], n: int, total: float, project: dict[str, Any],
                     se_events: list[dict[str, Any]], target: float) -> str:
    """loudnorm 2 パス目 (linear) 用のパラメータを 1 パス目で計測."""
    extra, chains = build_audio_graph(n, total, project, se_events, n,
                                      f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", limiter=False)
    cmd = ["ffmpeg", "-hide_banner", "-nostdin"]
    for a in seg_inputs + extra:
        cmd += a
    cmd += ["-filter_complex", ";".join(chains), "-map", "[aout]", "-f", "null", "-"]
    err = run(cmd, "ラウドネス計測")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    if not m:
        return f"loudnorm=I={target}:TP=-1.5:LRA=11"
    j = json.loads(m.group(0))
    return (f"loudnorm=I={target}:TP=-1.5:LRA=11:measured_I={j['input_i']}:measured_TP={j['input_tp']}"
            f":measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:offset={j['target_offset']}"
            f":linear=true")


def _check_rhythm(project: dict[str, Any], plans: list, telop_events: list[dict[str, Any]],
                  total: float) -> None:
    """仕様の頻度・配分の目安と比べてログに出す (警告のみ)."""
    minutes = max(total / 60.0, 1e-6)
    n_punch = sum(1 for pl in plans for e in pl.cut["effects"] if e["type"] in ("punch_zoom", "punch_in"))
    n_c = sum(1 for ev in telop_events if ev["group"] == "C")
    log.info("  パンチズーム %d 回 (%.1f 回/分)、ツッコミ %d 回 (%.1f 回/分)", n_punch, n_punch / minutes,
             n_c, n_c / minutes)
    if n_punch / minutes > P.PUNCH_MAX_PER_MIN:
        log.warning("パンチズームが %.1f 回/分です (仕様: 1分に%d回まで)", n_punch / minutes, P.PUNCH_MAX_PER_MIN)
    if n_c / minutes > P.TSUKKOMI_MAX_PER_MIN:
        log.warning("ツッコミが %.1f 回/分です (仕様: 1分に%d回まで)", n_c / minutes, P.TSUKKOMI_MAX_PER_MIN)
    grouped = [ev for ev in telop_events if ev["group"]]
    if grouped:
        tot = sum(ev["end"] - ev["start"] for ev in grouped)
        share = {g: sum(ev["end"] - ev["start"] for ev in grouped if ev["group"] == g) / tot for g in P.SHARE_TARGET}
        log.info("  プリセット配分 (表示時間): " + " / ".join(
            f"{g} {share[g] * 100:.0f}% (目安 {P.SHARE_TARGET[g] * 100:.0f}%)" for g in P.SHARE_TARGET))


def render(cutlist_path: str | Path, output: str | None = None, aspect: str | None = None,
           preview: bool = False, keep_temp: bool = False, dry_run: bool = False,
           project_path: str | None = None) -> dict[str, Any]:
    t_start = time.time()
    require_tools()
    cutlist_path = Path(cutlist_path)
    overrides: dict[str, Any] = {}
    if aspect:
        overrides["aspect"] = aspect
    if preview:
        overrides["resolution"] = 540
        overrides["video"] = {"preset": "veryfast", "crf": 26}
    doc = load_cutlist(cutlist_path, Path(project_path) if project_path else None, overrides)
    project, cuts, base = doc["project"], doc["cuts"], doc["_base_dir"]
    W, H = project["width"], project["height"]

    # CLI の -o はカレントディレクトリ基準、project.output はカット表のあるフォルダ基準
    out_path = Path(output) if output else Path(project["output"])
    if not out_path.is_absolute():
        out_path = ((Path.cwd() if output else base) / out_path).resolve()
    if aspect and not output:
        out_path = out_path.with_name(f"{out_path.stem}_{aspect.replace(':', 'x')}{out_path.suffix}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 実行ログを <出力名>.render.log にも残す
    fh = logging.FileHandler(out_path.with_suffix(".render.log"), mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
    fh.setLevel(logging.INFO)
    log.addHandler(fh)
    try:
        return _render(cutlist_path, project, cuts, base, out_path, keep_temp, dry_run, t_start)
    except Exception as e:
        log.error("レンダリング失敗: %s", str(e).splitlines()[0])
        raise
    finally:
        log.removeHandler(fh)
        fh.close()


def _render(cutlist_path: Path, project: dict[str, Any], cuts: list[dict[str, Any]], base: Path,
            out_path: Path, keep_temp: bool, dry_run: bool, t_start: float) -> dict[str, Any]:
    W, H = project["width"], project["height"]
    work = out_path.parent / f".work_{out_path.stem}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    log.info("プロジェクト: %s  %dx%d %sfps  aspect=%s  cuts=%d", project["title"], W, H, project["fps"],
             project["aspect"], len(cuts))
    used_styles = {t["style"] for c in cuts for t in c["telop"]}
    for name in sorted(used_styles):
        f = project["_fonts"][name]
        req = f["requested"] if isinstance(f["requested"], list) else [f["requested"]]
        if f["found"]:
            log.info("  フォント %-8s → %s", name, f["used"])
        else:
            log.info("  フォント %-8s → %s (候補 %s が未インストールのため代替)", name, f["used"], " / ".join(req))

    # 1) 計画 (長さ・ジャンプカット区間の決定)
    plans = [plan_cut(c, project, base) for c in cuts]

    # 2) セグメント
    seg_paths = []
    for i, plan in enumerate(plans):
        seg = work / f"seg_{i:03d}_{plan.cut['id']}.mkv"
        seg_paths.append(seg)
        if not dry_run:
            t0 = time.time()
            render_segment(plan, project, base, seg)
            log.info("  [%d/%d] %-14s %-7s %6.2fs  (%.1fs)%s", i + 1, len(plans), plan.cut["id"],
                     plan.cut["type"], plan.duration, time.time() - t0,
                     f"  jumpcut {plan.report['jumpcut']['before']}s→{plan.report['jumpcut']['after']}s"
                     if "jumpcut" in plan.report else "")

    # 3) タイムライン (実際のセグメント長で積算)
    timeline, cursor = [], 0.0
    for plan, seg in zip(plans, seg_paths):
        d = duration_of(seg) if not dry_run else plan.duration
        d = min(d, plan.duration) if d > plan.duration else d
        timeline.append({"id": plan.cut["id"], "type": plan.cut["type"], "start": round(cursor, 3),
                         "duration": round(d, 3), **plan.report})
        cursor += d
    total = round(cursor, 3)

    # 4) テロップ (ASS)
    ass = AssBuilder(project)
    se_events = []
    lib = project.get("se_library") or {}
    telop_events = []
    for plan, tl in zip(plans, timeline):
        for t in plan.cut["telop"]:
            ev = ass.add(t, tl["start"], tl["duration"], where=f"cut '{plan.cut['id']}'")
            if ev is None:
                continue
            telop_events.append(ev)
            # テロップ出現と同時 (0〜30ms 以内) に SE。プリセット A は "pop"
            se_name = t.get("se", project["styles"][t["style"]].get("se"))
            if se_name:
                se_file = lib.get(se_name, se_name)
                if se_name in lib or Path(resolve(base, se_file)).suffix:
                    p = resolve(base, se_file)
                    probe(p)
                    se_events.append({"file": se_file, "path": p, "time": ev["start"],
                                      "volume": t.get("se_volume", 1.0)})
                else:
                    log.warning("テロップ SE '%s' が project.se_library にありません (スキップ)", se_name)
        for se in plan.cut["se"]:
            p = resolve(base, se["file"])
            probe(p)
            se_events.append({**se, "path": p, "time": tl["start"] + float(se.get("at", 0.0))})
    _check_rhythm(project, plans, telop_events, total)
    ass_path = work / "telops.ass"
    ass_path.write_text(ass.render(), encoding="utf-8")

    report: dict[str, Any] = {
        "cutlist": str(cutlist_path), "output": str(out_path), "aspect": project["aspect"],
        "resolution": f"{W}x{H}", "fps": project["fps"], "planned_duration": total,
        "timeline": timeline, "rendered_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    tgt = project.get("target_duration")
    if tgt:
        diff = total - float(tgt)
        report["target_duration"] = tgt
        if abs(diff) > 0.5:
            log.warning("合計 %.2fs は目標 %ss から %+.2fs ずれています", total, tgt, diff)
    if dry_run:
        report["status"] = "dry-run"
        log.info("dry-run: ASS を %s に出力しました", ass_path)
        return report

    # 5) 最終合成
    n = len(seg_paths)
    seg_inputs = [["-i", str(p)] for p in seg_paths]
    a_cfg = project.get("audio", {})
    loud = None
    if a_cfg.get("loudnorm", True):
        loud = measure_loudness(seg_inputs, n, total, project, se_events, float(a_cfg.get("target_lufs", -14)))
    extra_inputs, a_chains = build_audio_graph(n, total, project, se_events, n, loud)

    v_chains = [f"{''.join(f'[{i}:v]' for i in range(n))}concat=n={n}:v=1:a=0[vcat]"]
    ass_opt = "filename=telops.ass"
    if project.get("fonts_dir"):
        ass_opt += f":fontsdir={_filter_quote(resolve(base, project['fonts_dir']))}"
    v_chains.append(f"[vcat]ass={ass_opt}[vout]")
    vcfg = project.get("video", {})
    cmd = ["ffmpeg", "-hide_banner", "-y", "-nostdin"]
    for a in seg_inputs + extra_inputs:
        cmd += a
    cmd += ["-filter_complex", ";\n".join(v_chains + a_chains), "-map", "[vout]", "-map", "[aout]",
            "-t", f"{total:.3f}", "-r", str(project["fps"]),
            "-c:v", "libx264", "-preset", str(vcfg.get("preset", "medium")), "-crf", str(vcfg.get("crf", 20)),
            "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", "-metadata", f"title={project['title']}", str(out_path)]
    t0 = time.time()
    run(cmd, "最終合成", cwd=work)
    log.info("  最終合成 (テロップ/BGM/SE/ラウドネス) %.1fs", time.time() - t0)

    info = probe(out_path)
    report.update({
        "status": "success", "duration": round(info.duration or 0, 3),
        "size_bytes": out_path.stat().st_size, "elapsed_sec": round(time.time() - t_start, 1),
        "video": f"{info.width}x{info.height}", "bgm": bool(project.get("bgm")),
        "se_count": len(se_events), "loudnorm": loud is not None,
    })
    log_path = out_path.with_suffix(".render.json")
    log_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy(ass_path, out_path.with_suffix(".ass"))
    if not keep_temp:
        shutil.rmtree(work, ignore_errors=True)
    log.info("完了: %s  (%.2fs, %.1f MB, %s)", out_path, report["duration"],
             report["size_bytes"] / 1e6, report["video"])
    return report
