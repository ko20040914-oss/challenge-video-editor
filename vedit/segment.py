"""カット 1 本 → 中間セグメント (映像効果・ジャンプカット・ワイプ・ナレーション込み) の生成."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .media import MediaInfo, RenderError, detect_silence, probe, run

log = logging.getLogger("vedit")

MOTION = {"zoom_in", "zoom_out", "kenburns", "pan", "punch_in"}
OTHER = {"shake", "fade_in", "fade_out", "flash"}


def resolve(base: Path, p: str) -> str:
    if p.startswith("color:"):
        return p
    q = Path(p)
    return str(q if q.is_absolute() else (base / q).resolve())


# ---------------------------------------------------------------- jump cut
def keep_intervals(silences: list[tuple[float, float]], total: float, padding: float,
                   min_keep: float) -> list[tuple[float, float]]:
    """無音区間の補集合 (= 喋っている区間) を padding だけ広げて返す."""
    keeps, cur = [], 0.0
    for s, e in silences:
        if s > cur:
            keeps.append((cur, s))
        cur = max(cur, e)
    if cur < total:
        keeps.append((cur, total))
    padded = [(max(0.0, a - padding), min(total, b + padding)) for a, b in keeps]
    merged: list[list[float]] = []
    for a, b in padded:
        if merged and a <= merged[-1][1] + 1e-3:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a >= min_keep]


def select_expr(keeps: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keeps)


# ---------------------------------------------------------------- plan
@dataclass
class CutPlan:
    cut: dict[str, Any]
    src: MediaInfo
    duration: float
    src_in: float = 0.0
    src_len: float | None = None
    keeps: list[tuple[float, float]] | None = None          # 映像+元音声のジャンプカット
    narr_keeps: list[tuple[float, float]] | None = None     # ナレーションのジャンプカット
    narr: MediaInfo | None = None
    narr_len: float = 0.0
    report: dict[str, Any] = field(default_factory=dict)


def plan_cut(cut: dict[str, Any], project: dict[str, Any], base: Path) -> CutPlan:
    src = probe(resolve(base, cut["source"]))
    if src.kind == "audio":
        raise RenderError(f"cut '{cut['id']}': source に音声のみの素材は使えません (narration に指定してください)")
    plan = CutPlan(cut=cut, src=src, duration=0.0)
    jc = cut.get("jumpcut")
    jc_target = (jc or {}).get("target") or ("source" if src.kind == "video" and src.has_audio else "narration")

    if src.kind == "video":
        plan.src_in = float(cut.get("in") or 0.0)
        end = float(cut["out"]) if cut.get("out") is not None else src.duration
        plan.src_len = max(0.0, end - plan.src_in)
        if plan.src_len <= 0:
            raise RenderError(f"cut '{cut['id']}': in/out が不正です")
        natural = plan.src_len
        if jc and jc_target == "source":
            if not src.has_audio:
                raise RenderError(f"cut '{cut['id']}': 音声のない動画にはジャンプカットできません")
            sil = detect_silence(src.path, jc["noise"], jc["min_silence"], plan.src_in, plan.src_len)
            plan.keeps = keep_intervals(sil, plan.src_len, jc["padding"], jc["min_keep"])
            natural = sum(b - a for a, b in plan.keeps)
            plan.report["jumpcut"] = {"target": "source", "before": round(plan.src_len, 2),
                                      "after": round(natural, 2), "segments": len(plan.keeps)}
    else:
        natural = None

    narr = cut.get("narration")
    if narr:
        plan.narr = probe(resolve(base, narr["file"]))
        plan.narr_len = plan.narr.duration or 0.0
        if jc and jc_target == "narration":
            sil = detect_silence(plan.narr.path, jc["noise"], jc["min_silence"])
            plan.narr_keeps = keep_intervals(sil, plan.narr_len, jc["padding"], jc["min_keep"])
            before = plan.narr_len
            plan.narr_len = sum(b - a for a, b in plan.narr_keeps)
            plan.report["jumpcut"] = {"target": "narration", "before": round(before, 2),
                                      "after": round(plan.narr_len, 2), "segments": len(plan.narr_keeps)}

    narr_total = (float(narr["offset"]) + plan.narr_len + project["defaults"]["narration_tail"]) if narr else None
    dur = cut.get("duration")
    if dur == "narration":
        if narr_total is None:
            raise RenderError(f"cut '{cut['id']}': duration=narration ですが narration がありません")
        dur = narr_total
    elif dur is None:
        dur = natural if natural is not None else (narr_total if narr_total is not None else 3.0)
    dur = float(dur)
    if narr_total is not None and narr_total - project["defaults"]["narration_tail"] > dur + 0.05:
        log.warning("cut '%s': ナレーション(%.2fs)がカット長(%.2fs)より長いので途中で切れます。"
                    "duration を \"narration\" にすると自動で合わせます", cut["id"], narr_total, dur)
    plan.duration = round(dur, 3)
    return plan


# ---------------------------------------------------------------- filters
def _ease(p: str, ease: str) -> str:
    if ease == "linear":
        return p
    if ease == "out":
        return f"(1-pow(1-{p},2))"
    if ease == "in":
        return f"pow({p},2)"
    return f"(0.5-0.5*cos(PI*{p}))"


def motion_filter(eff: dict[str, Any], W: int, H: int, fps: int, dur: float) -> str:
    """ズーム / パン / Ken Burns を zoompan で実現 (入力は 2W x 2H にスケール済み)."""
    t = eff["type"]
    if t == "zoom_in":
        a = {"zoom": eff.get("from", 1.0), "x": eff.get("x", 0.5), "y": eff.get("y", 0.5)}
        b = {"zoom": eff.get("to", 1.2), "x": eff.get("x", 0.5), "y": eff.get("y", 0.5)}
    elif t == "punch_in":  # 強調用の一瞬のズーム (「ここ大事!」)
        eff = {"start": 0.0, "duration": 0.12, "ease": "out", **eff}
        a = {"zoom": 1.0, "x": 0.5, "y": 0.5}
        b = {"zoom": eff.get("to", 1.3), "x": eff.get("x", 0.5), "y": eff.get("y", 0.5)}
    elif t == "zoom_out":
        a = {"zoom": eff.get("from", 1.2), "x": eff.get("x", 0.5), "y": eff.get("y", 0.5)}
        b = {"zoom": eff.get("to", 1.0), "x": eff.get("x", 0.5), "y": eff.get("y", 0.5)}
    elif t == "pan":
        z = eff.get("zoom", 1.2)
        d = eff.get("direction", "right")
        lo, hi = 0.0, 1.0
        pairs = {"right": ((lo, .5), (hi, .5)), "left": ((hi, .5), (lo, .5)),
                 "down": ((.5, lo), (.5, hi)), "up": ((.5, hi), (.5, lo))}
        if d not in pairs:
            raise RenderError(f"pan の direction が不正: {d}")
        (ax, ay), (bx, by) = pairs[d]
        a = {"zoom": z, "x": ax, "y": ay}
        b = {"zoom": z, "x": bx, "y": by}
    else:  # kenburns
        a = {"zoom": 1.0, "x": 0.5, "y": 0.5, **eff.get("from", {})}
        b = {"zoom": 1.25, "x": 0.5, "y": 0.5, **eff.get("to", {})}
    start = float(eff.get("start", 0.0))
    length = float(eff.get("duration", dur - start))
    length = max(length, 1.0 / fps)
    p = _ease(f"clip((on/{fps}-{start})/{length},0,1)", eff.get("ease", "inout"))
    z = f"{a['zoom']}+({b['zoom']}-{a['zoom']})*{p}"
    cx = f"({a['x']}+({b['x']}-{a['x']})*{p})"
    cy = f"({a['y']}+({b['y']}-{a['y']})*{p})"
    x = f"clip({cx}*iw-iw/zoom/2,0,iw-iw/zoom)"
    y = f"clip({cy}*ih-ih/zoom/2,0,ih-ih/zoom)"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={fps}"


def shake_filter(shakes: list[dict[str, Any]], W: int, H: int) -> str:
    margin = 0.035
    sw, sh = (int(W * (1 + 2 * margin)) // 2) * 2, (int(H * (1 + 2 * margin)) // 2) * 2
    mx, my = (sw - W) // 2, (sh - H) // 2
    xs, ys = [], []
    for i, s in enumerate(shakes):
        st = float(s.get("start", 0.0)); d = float(s.get("duration", 0.35))
        amp = min(float(s.get("amplitude", 14)), mx); f = float(s.get("frequency", 18))
        env = f"{amp}*(1-(t-{st})/{d})"
        win = f"between(t,{st},{st + d})"
        xs.append(f"{win}*{env}*sin(2*PI*{f}*(t-{st})+{i})")
        ys.append(f"{win}*{env}*cos(2*PI*{f * 1.3:.2f}*(t-{st})+{i * 2})")
    return (f"scale={sw}:{sh},crop={W}:{H}:x='{mx}+clip({'+'.join(xs)},-{mx},{mx})'"
            f":y='{my}+clip({'+'.join(ys)},-{my},{my})'")


def fit_filter(fit: str, w: int, h: int, lbl_in: str, lbl_out: str) -> list[str]:
    cover = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    contain = f"scale={w}:{h}:force_original_aspect_ratio=decrease"
    if fit == "cover":
        return [f"[{lbl_in}]{cover},setsar=1[{lbl_out}]"]
    if fit == "contain":
        return [f"[{lbl_in}]{contain},pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[{lbl_out}]"]
    if fit == "blur":  # 縦動画などで余白をぼかし背景で埋める
        return [f"[{lbl_in}]split[fa][fb]",
                f"[fa]{cover},gblur=sigma=30,eq=brightness=-0.08[fbg]",
                f"[fb]{contain}[ffg]",
                f"[fbg][ffg]overlay=(W-w)/2:(H-h)/2,setsar=1[{lbl_out}]"]
    raise RenderError(f"fit は cover / contain / blur のいずれか: {fit}")


PIP_POS = {"tl": ("{m}", "{m}"), "tr": ("W-w-{m}", "{m}"), "bl": ("{m}", "H-h-{m}"),
           "br": ("W-w-{m}", "H-h-{m}"), "center": ("(W-w)/2", "(H-h)/2")}


class Graph:
    def __init__(self):
        self.inputs: list[list[str]] = []
        self.chains: list[str] = []

    def add_input(self, args: list[str]) -> int:
        self.inputs.append(args)
        return len(self.inputs) - 1


def source_input(g: Graph, src: MediaInfo, fps: int, dur: float, W: int, H: int,
                 ss: float = 0.0, length: float | None = None, loop: bool = False) -> int:
    if src.kind == "color":
        return g.add_input(["-f", "lavfi", "-i", f"color=c={src.color.replace('#', '0x')}:s={W}x{H}:r={fps}:d={dur + 1:.3f}"])
    if src.kind == "image":
        return g.add_input(["-loop", "1", "-framerate", str(fps), "-t", f"{dur + 1:.3f}", "-i", str(src.path)])
    args = []
    if loop:
        args += ["-stream_loop", "-1"]
    if ss:
        args += ["-ss", f"{ss:.3f}"]
    if length is not None:
        args += ["-t", f"{length:.3f}"]
    return g.add_input(args + ["-i", str(src.path)])


def build_segment(plan: CutPlan, project: dict[str, Any], base: Path, out_path: Path) -> list[str]:
    cut, W, H, fps, dur = plan.cut, project["width"], project["height"], int(project["fps"]), plan.duration
    g = Graph()
    vi = source_input(g, plan.src, fps, dur, W, H, plan.src_in, plan.src_len)

    effects = cut["effects"]
    for e in effects:
        if e.get("type") not in MOTION | OTHER:
            raise RenderError(f"cut '{cut['id']}': 未知の効果 {e.get('type')} (使えるもの: {sorted(MOTION | OTHER)})")
    motions = [e for e in effects if e["type"] in MOTION]
    if len(motions) > 1:
        raise RenderError(f"cut '{cut['id']}': ズーム/パン系の効果は 1 カットに 1 つまでです")

    # --- video
    v = f"[{vi}:v]fps={fps},setpts=PTS-STARTPTS"
    if plan.keeps:
        v += f",select='{select_expr(plan.keeps)}',setpts=N/{fps}/TB"
    g.chains.append(v + "[v0]")
    fw, fh = (W * 2, H * 2) if motions else (W, H)
    g.chains += fit_filter(cut["fit"], fw, fh, "v0", "v1")
    cur = "v1"
    if motions:
        g.chains.append(f"[{cur}]{motion_filter(motions[0], W, H, fps, dur)}[v2]")
        cur = "v2"
    shakes = [e for e in effects if e["type"] == "shake"]
    if shakes:
        g.chains.append(f"[{cur}]{shake_filter(shakes, W, H)}[v3]")
        cur = "v3"

    for i, pip in enumerate(cut["pip"]):
        psrc = probe(resolve(base, pip["source"]))
        pi = source_input(g, psrc, fps, dur, W, H, float(pip.get("in", 0.0)), loop=psrc.kind == "video")
        st = float(pip.get("start", 0.0)); en = float(pip.get("end", dur))
        pw = int(W * float(pip.get("scale", 0.32))) // 2 * 2
        b = int(pip.get("border", 6))
        bc = pip.get("border_color", "#FFFFFF").replace("#", "0x")
        m = int(min(W, H) * float(pip.get("margin", 0.04)))
        pos = pip.get("position", "br")
        if isinstance(pos, (list, tuple)):
            px, py = f"{pos[0]}*W-w/2", f"{pos[1]}*H-h/2"
        else:
            if pos not in PIP_POS:
                raise RenderError(f"pip position は {list(PIP_POS)} か [x, y]: {pos}")
            px, py = (s.format(m=m) for s in PIP_POS[pos])
        g.chains.append(
            f"[{pi}:v]fps={fps},setpts=PTS-STARTPTS,scale={pw}:-2,setsar=1,"
            f"pad=iw+{2 * b}:ih+{2 * b}:{b}:{b}:color={bc},format=yuva420p,"
            f"fade=t=in:st=0:d=0.25:alpha=1,setpts=PTS+{st}/TB[pip{i}]")
        g.chains.append(f"[{cur}][pip{i}]overlay=x='{px}':y='{py}':enable='between(t,{st},{en})'"
                        f":eof_action=pass[vp{i}]")
        cur = f"vp{i}"

    tail = [f"tpad=stop_mode=clone:stop_duration={dur + 1:.3f}", f"trim=duration={dur:.3f}"]
    for e in effects:
        d = float(e.get("duration", 0.3))
        if e["type"] == "fade_in":
            tail.append(f"fade=t=in:st=0:d={d}:color={e.get('color', 'black')}")
        elif e["type"] == "flash":
            # カット頭で白(指定色)から明ける = フラッシュ
            tail.append(f"fade=t=in:st=0:d={d}:color={e.get('color', 'white')}")
        elif e["type"] == "fade_out":
            tail.append(f"fade=t=out:st={max(0.0, dur - d):.3f}:d={d}:color={e.get('color', 'black')}")
    tail.append("format=yuv420p,setsar=1")
    g.chains.append(f"[{cur}]{','.join(tail)}[vout]")

    # --- audio
    amix = []
    afmt = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    vol = float(cut.get("source_volume", 1.0))
    if plan.src.kind == "video" and plan.src.has_audio and vol > 0:
        a = f"[{vi}:a]{afmt},asetpts=PTS-STARTPTS"
        if plan.keeps:
            a += f",asetnsamples=n=480,aselect='{select_expr(plan.keeps)}',asetpts=N/SR/TB"
        g.chains.append(a + f",volume={vol}[asrc]")
        amix.append("[asrc]")
    if plan.narr:
        ni = g.add_input(["-i", str(plan.narr.path)])
        narr = cut["narration"]
        a = f"[{ni}:a]{afmt},asetpts=PTS-STARTPTS"
        if plan.narr_keeps:
            a += f",asetnsamples=n=480,aselect='{select_expr(plan.narr_keeps)}',asetpts=N/SR/TB"
        off = int(float(narr["offset"]) * 1000)
        g.chains.append(a + f",volume={float(narr['volume'])},adelay={off}:all=1[anarr]")
        amix.append("[anarr]")
    if not amix:
        si = g.add_input(["-f", "lavfi", "-t", f"{dur + 1:.3f}", "-i", "anullsrc=r=48000:cl=stereo"])
        amix.append(f"[{si}:a]")
    mixed = f"{''.join(amix)}amix=inputs={len(amix)}:normalize=0:duration=longest," if len(amix) > 1 \
        else amix[0]
    afade = []
    for e in effects:
        if e["type"] == "fade_out" and e.get("audio", True):
            d = float(e.get("duration", 0.3))
            afade.append(f"afade=t=out:st={max(0.0, dur - d):.3f}:d={d}")
    chain = ",".join(["apad", f"atrim=duration={dur:.3f}", *afade, "asetpts=PTS-STARTPTS"])
    g.chains.append(f"{mixed}{chain}[aout]")

    cmd = ["ffmpeg", "-hide_banner", "-y", "-nostdin"]
    for args in g.inputs:
        cmd += args
    cmd += ["-filter_complex", ";\n".join(g.chains), "-map", "[vout]", "-map", "[aout]",
            "-t", f"{dur:.3f}", "-r", str(fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(out_path)]
    return cmd


def render_segment(plan: CutPlan, project: dict[str, Any], base: Path, out_path: Path) -> None:
    cmd = build_segment(plan, project, base, out_path)
    run(cmd, f"カット '{plan.cut['id']}' のレンダリング")
