"""テロップ → ASS 字幕の生成.

テキスト記法:
  **強調**                    スタイルの highlight (色・サイズ・跳ね) を適用
  !!警告!!                    警告色 (warn_color, 既定 #FF2E3D) + 跳ね
  {c=#FF0000,s=1.4,oc=#000}語{/}  任意の色(c) / 倍率(s) / 縁色(oc) を単語単位で指定
  |                           単語の区切り (表示されない。words / karaoke アニメの単位)
  改行 (\n)                   改行
アニメーション (anim):
  none / fade / pop(拡大しながら出現) / slide_up / words(単語ごとにポップ表示)
  / karaoke(単語ごとに色が変わる) / typewriter(1文字ずつ表示)
  / pop_slide(ポップイン+スライドアップ: プリセット A) / bounce(全体が115%に跳ねる: プリセット B)
アニメの速度・倍率はスタイルの pop_from / pop_ms / pop_overshoot / slide_px / slide_ms /
bounce / bounce_ms / fade_out_ms で決まる (プリセット A/B/C は presets.py の仕様値)。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

ALIGN = {"bottom_left": 1, "bottom": 2, "bottom_right": 3, "left": 4, "center": 5, "right": 6,
         "top_left": 7, "top": 8, "top_right": 9}
ANIMS = {"none", "fade", "pop", "slide_up", "words", "karaoke", "typewriter", "pop_slide", "bounce"}
log = logging.getLogger("vedit")


def ass_color(hex_color: str, alpha: float = 0.0) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"色は #RRGGBB で指定してください: {hex_color}")
    r, g, b = h[0:2], h[2:4], h[4:6]
    a = max(0, min(255, round(alpha * 255)))
    return f"&H{a:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def align_num(a: Any) -> int:
    if isinstance(a, int):
        return a
    return ALIGN[str(a)]


@dataclass
class Run:
    text: str
    word: int
    color: str | None = None
    scale: float = 1.0
    outline_color: str | None = None
    bounce: float | None = None   # 強調語の跳ね倍率 (例 1.15)
    emph: bool = False            # ** ** / !! !! で強調された語か


_TOKEN = re.compile(r"\*\*(?P<hl>.+?)\*\*|!!(?P<warn>.+?)!!|\{(?P<attrs>[a-z]+=[^}]*)\}(?P<span>.*?)\{/\}", re.S)


def _escape(s: str) -> str:
    return s.replace("\\", "＼").replace("{", "｛").replace("}", "｝")


def parse_markup(text: str, highlight: dict[str, Any], per_char: bool = False,
                 warn_color: str | None = None) -> list[Run]:
    segments: list[tuple[str, dict[str, Any]]] = []
    pos = 0
    for m in _TOKEN.finditer(text):
        if m.start() > pos:
            segments.append((text[pos:m.start()], {}))
        if m.group("hl") is not None:
            segments.append((m.group("hl"), {"color": highlight.get("color"),
                                             "scale": highlight.get("scale", 1.0),
                                             "outline_color": highlight.get("outline_color"),
                                             "bounce": highlight.get("bounce"), "emph": True}))
        elif m.group("warn") is not None:
            segments.append((m.group("warn"), {"color": warn_color or "#FF2E3D",
                                               "scale": highlight.get("scale", 1.0),
                                               "bounce": highlight.get("bounce"), "emph": True}))
        else:
            attrs: dict[str, Any] = {}
            for kv in m.group("attrs").split(","):
                k, _, v = kv.partition("=")
                k, v = k.strip(), v.strip()
                if k == "c":
                    attrs["color"] = v
                elif k == "s":
                    attrs["scale"] = float(v)
                elif k == "oc":
                    attrs["outline_color"] = v
            segments.append((m.group("span"), attrs))
        pos = m.end()
    if pos < len(text):
        segments.append((text[pos:], {}))

    runs: list[Run] = []
    word = -1
    for seg_text, attrs in segments:
        if per_char:
            pieces = [ch for ch in seg_text.replace("|", "")]
        else:
            # '|' と空白で単語分割 (空白は直前の単語に残す)
            pieces = [p for p in re.split(r"(?<=\s)|\|", seg_text) if p]
        for p in pieces:
            if p == "\n":
                runs.append(Run("\\N", word if word >= 0 else 0))
                continue
            word += 1
            out = _escape(p).replace("\n", "\\N")
            runs.append(Run(out, word, attrs.get("color"), attrs.get("scale", 1.0), attrs.get("outline_color"),
                            attrs.get("bounce"), attrs.get("emph", False)))
    return runs


class AssBuilder:
    def __init__(self, project: dict[str, Any]):
        self.p = project
        self.W, self.H = project["width"], project["height"]
        self.k = min(self.W, self.H) / 1080.0
        self.styles = project["styles"]
        self.events: list[str] = []

    # -- styles -------------------------------------------------------------
    def _style_line(self, name: str, st: dict[str, Any], outer: bool) -> str:
        k = self.k
        font = st.get("font") or self.p["font"]
        primary = ass_color(st["color"])
        if st.get("box"):
            border_style = 3
            outline_c = ass_color(st["box_color"], st.get("box_alpha", 0.0))
            back_c = outline_c
            outline = max(st.get("outline", 0), 12) * k
            shadow = 0
        else:
            border_style = 1
            shadow_c = ass_color(st.get("shadow_color", "#000000"), 0.45)
            if outer:
                outline_c = ass_color(st["outer_outline_color"])
                outline = (st["outline"] + st["outer_outline"]) * k
                shadow = st.get("shadow", 0) * k
            else:
                outline_c = ass_color(st["outline_color"])
                outline = st["outline"] * k
                shadow = 0 if self._has_outer(st) else st.get("shadow", 0) * k
            back_c = shadow_c
        a = align_num(st["align"])
        return ("Style: {n},{f},{size:.0f},{pc},{pc},{oc},{bc},{b},0,0,0,100,100,{sp},0,{bs},{o:.1f},{s:.1f},"
                "{a},{ml:.0f},{mr:.0f},{mv:.0f},1").format(
            n=name, f=font, size=st["size"] * k, pc=primary, oc=outline_c, bc=back_c,
            b=-1 if st.get("bold", True) else 0, bs=border_style,
            sp=round(st["size"] * k * st["spacing_pct"], 1) if st.get("spacing_pct") else st.get("spacing", 0),
            o=outline, s=shadow, a=a, ml=st["margin_h"] * k, mr=st["margin_h"] * k, mv=st["margin_v"] * k)

    @staticmethod
    def style_name(name: str) -> str:
        # libass は "Default" という名前を特別扱いするので接頭辞を付ける
        return f"T_{name}"

    @staticmethod
    def _has_outer(st: dict[str, Any]) -> bool:
        return bool(st.get("outer_outline_color")) and st.get("outer_outline", 0) > 0 and not st.get("box")

    # -- events -------------------------------------------------------------
    def add(self, telop: dict[str, Any], cut_start: float, cut_dur: float,
            where: str = "") -> dict[str, Any] | None:
        """テロップ 1 つを追加. 戻り値: {start, end, style, group} (表示されない場合 None)"""
        st = self.styles[telop["style"]]
        anim = telop.get("anim") or st.get("anim", "none")
        if anim not in ANIMS:
            raise ValueError(f"未知のテロップアニメ: {anim} (使えるもの: {sorted(ANIMS)})")
        t0 = float(telop.get("start", 0.0))
        t1 = telop.get("end")
        t1 = cut_dur if t1 is None else (cut_dur + float(t1) if float(t1) < 0 else float(t1))
        t1 = min(t1, cut_dur)
        if t1 - t0 < 0.05:
            return None
        dur_ms = (t1 - t0) * 1000
        runs = parse_markup(str(telop["text"]), st.get("highlight", {}), per_char=(anim == "typewriter"),
                            warn_color=st.get("warn_color"))
        n_words = max((r.word for r in runs), default=0) + 1
        self._validate(runs, st, n_words, f"{where} 「{telop['text']}」")
        interval = telop.get("word_interval")
        if interval is None:
            interval = min(0.22, max(0.05, (t1 - t0) * 0.5 / n_words))
        interval_ms = float(interval) * 1000

        line = self._line_tags(st, anim, telop)
        sname = self.style_name(telop["style"])
        layers = [(0, f"{sname}__outer", True), (1, sname, False)] if self._has_outer(st) \
            else [(1, sname, False)]
        for layer, lname, outer in layers:
            body = "".join(self._run(r, anim, st, outer, r.word * interval_ms, dur_ms) for r in runs)
            self.events.append(
                f"Dialogue: {layer},{ass_time(cut_start + t0)},{ass_time(cut_start + t1)},{lname},,0,0,0,,"
                f"{{{line}}}{body}")
        return {"start": cut_start + t0, "end": cut_start + t1, "style": telop["style"],
                "group": st.get("preset_group")}

    def _validate(self, runs: list[Run], st: dict[str, Any], n_words: int, label: str) -> None:
        """仕様の文字量・色数・強調語数の上限を超えていたら警告する (レンダリングは続ける)."""
        lines = "".join(r.text for r in runs).split("\\N")
        if st.get("line_max_chars") and max(len(x.strip()) for x in lines) > st["line_max_chars"]:
            log.warning("テロップ %s: 1行 %d 文字は上限 %d 文字を超えています", label,
                        max(len(x.strip()) for x in lines), st["line_max_chars"])
        if st.get("max_lines") and len(lines) > st["max_lines"]:
            log.warning("テロップ %s: %d 行は上限 %d 行を超えています", label, len(lines), st["max_lines"])
        colors = {(r.color or st["color"]).upper() for r in runs if r.text.strip() and r.text != "\\N"}
        if st.get("max_colors") and len(colors) > st["max_colors"]:
            log.warning("テロップ %s: %d 色使っています (1画面%d色まで)", label, len(colors), st["max_colors"])
        if st.get("max_emph_words"):
            n_emph = n_words if st.get("check_words") else sum(1 for r in runs if r.emph)
            if n_emph > st["max_emph_words"]:
                log.warning("テロップ %s: 強調が %d 語あります (1文%d語まで)", label, n_emph, st["max_emph_words"])

    def _anchor(self, st: dict[str, Any]) -> tuple[float, float]:
        a = align_num(st["align"])
        mh, mv = st["margin_h"] * self.k, st["margin_v"] * self.k
        x = {1: mh, 4: mh, 7: mh, 3: self.W - mh, 6: self.W - mh, 9: self.W - mh}.get(a, self.W / 2)
        y = {7: mv, 8: mv, 9: mv, 4: self.H / 2, 5: self.H / 2, 6: self.H / 2}.get(a, self.H - mv)
        return x, y

    def _line_tags(self, st: dict[str, Any], anim: str, telop: dict[str, Any]) -> str:
        tags = f"\\an{align_num(st['align'])}"
        if "pos" in telop:  # 任意配置 [x, y] (0-1 の割合)
            px, py = telop["pos"]
            tags += f"\\pos({px * self.W:.0f},{py * self.H:.0f})"
        fo = int(st.get("fade_out_ms", 120))
        if anim in ("pop", "bounce"):
            tags += f"\\fad(0,{fo})"
        elif anim == "pop_slide":
            x, y = (telop["pos"][0] * self.W, telop["pos"][1] * self.H) if "pos" in telop else self._anchor(st)
            off = st["slide_px"] * self.k
            tags += f"\\move({x:.0f},{y + off:.0f},{x:.0f},{y:.0f},0,{int(st['slide_ms'])})\\fad(0,{fo})"
        elif anim == "fade":
            tags += "\\fad(200,150)"
        elif anim == "slide_up":
            x, y = (telop["pos"][0] * self.W, telop["pos"][1] * self.H) if "pos" in telop else self._anchor(st)
            off = 50 * self.k
            tags += f"\\move({x:.0f},{y + off:.0f},{x:.0f},{y:.0f},0,220)\\fad(160,120)"
        elif anim in ("words", "karaoke", "typewriter"):
            tags += f"\\fad(0,{fo})"
        return tags

    def _run(self, r: Run, anim: str, st: dict[str, Any], outer: bool, delay: float, dur_ms: float) -> str:
        s = 100 * r.scale
        tags = []
        if not outer:
            # ASS の上書きタグは後続の文字にも残るので、毎回スタイル既定値を明示して戻す
            c = ass_color(r.color or st["color"])
            tags.append(f"\\1c{c[:2]}{c[4:]}&")
            if not st.get("box"):
                oc = ass_color(r.outline_color or st["outline_color"])
                tags.append(f"\\3c{oc[:2]}{oc[4:]}&")
        bounce = r.bounce or (st.get("bounce") if anim == "bounce" else None)
        if bounce and anim in ("pop", "pop_slide", "bounce", "fade", "none", "slide_up"):
            # 強調語の跳ね: (ポップイン開始倍率→) 115% → 100% を bounce_ms で
            start = s * st.get("pop_from", 1.0) if anim in ("pop", "pop_slide") else s
            bm = int(st.get("bounce_ms", 100))
            tags.append(f"\\fscx{start:.0f}\\fscy{start:.0f}\\t(0,{bm // 2},\\fscx{s * bounce:.0f}\\fscy{s * bounce:.0f})"
                        f"\\t({bm // 2},{bm},\\fscx{s:.0f}\\fscy{s:.0f})")
        elif anim in ("pop", "pop_slide"):
            pf, pm, ov = st.get("pop_from", 0.0), int(st.get("pop_ms", 200)), st.get("pop_overshoot")
            if ov:
                mid = int(pm * 0.55)
                tags.append(f"\\fscx{s * pf:.0f}\\fscy{s * pf:.0f}\\t(0,{mid},\\fscx{s * ov:.0f}\\fscy{s * ov:.0f})"
                            f"\\t({mid},{pm},\\fscx{s:.0f}\\fscy{s:.0f})")
            else:  # 仕様: 90% → 100% を 80〜120ms
                tags.append(f"\\fscx{s * pf:.0f}\\fscy{s * pf:.0f}\\t(0,{pm},\\fscx{s:.0f}\\fscy{s:.0f})")
        elif anim == "words" and bounce:
            d, bm = int(delay), int(st.get("bounce_ms", 100))
            tags.append(f"\\fscx{s:.0f}\\fscy{s:.0f}\\alpha&HFF&\\t({d},{d + 1},\\alpha&H00&)"
                        f"\\t({d},{d + bm // 2},\\fscx{s * bounce:.0f}\\fscy{s * bounce:.0f})"
                        f"\\t({d + bm // 2},{d + bm},\\fscx{s:.0f}\\fscy{s:.0f})")
        elif anim == "words":
            d = int(delay)
            # 縦方向だけ弾ませる (横幅が変わらないので行がガタつかない)
            tags.append(f"\\fscx{s:.0f}\\fscy{s * 0.3:.0f}\\alpha&HFF&\\t({d},{d + 1},\\alpha&H00&)"
                        f"\\t({d},{d + 90},\\fscy{s * 1.25:.0f})\\t({d + 90},{d + 170},\\fscy{s:.0f})")
        elif anim == "typewriter":
            d = int(delay)
            tags.append(f"\\fscx{s:.0f}\\fscy{s:.0f}\\alpha&HFF&\\t({d},{d + 1},\\alpha&H00&)")
        elif anim == "karaoke":
            tags.append(f"\\fscx{s:.0f}\\fscy{s:.0f}")
            if not outer:
                hl = st.get("highlight", {}).get("color", "#FFE600")
                c = ass_color(hl)
                d = int(delay)
                tags.append(f"\\t({d},{d + 80},\\1c{c[:2]}{c[4:]}&)")
        else:
            tags.append(f"\\fscx{s:.0f}\\fscy{s:.0f}")
        return ("{" + "".join(tags) + "}" if tags else "") + r.text

    def render(self) -> str:
        style_lines = []
        for name, st in self.styles.items():
            sname = self.style_name(name)
            style_lines.append(self._style_line(sname, st, outer=False))
            if self._has_outer(st):
                style_lines.append(self._style_line(f"{sname}__outer", st, outer=True))
        return "\n".join([
            "[Script Info]",
            f"Title: {self.p.get('title', '')}",
            "ScriptType: v4.00+",
            f"PlayResX: {self.W}",
            f"PlayResY: {self.H}",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.709",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            *style_lines,
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            *self.events,
            "",
        ])
