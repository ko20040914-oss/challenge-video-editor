"""テロップ → ASS 字幕の生成.

テキスト記法:
  **強調**                    スタイルの highlight (色・サイズ) を適用
  {c=#FF0000,s=1.4,oc=#000}語{/}  任意の色(c) / 倍率(s) / 縁色(oc) を単語単位で指定
  |                           単語の区切り (表示されない。words / karaoke アニメの単位)
  改行 (\n)                   改行
アニメーション (anim):
  none / fade / pop(拡大しながら出現) / slide_up / words(単語ごとにポップ表示)
  / karaoke(単語ごとに色が変わる) / typewriter(1文字ずつ表示)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ALIGN = {"bottom_left": 1, "bottom": 2, "bottom_right": 3, "left": 4, "center": 5, "right": 6,
         "top_left": 7, "top": 8, "top_right": 9}
ANIMS = {"none", "fade", "pop", "slide_up", "words", "karaoke", "typewriter"}


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


_TOKEN = re.compile(r"\*\*(?P<hl>.+?)\*\*|\{(?P<attrs>[a-z]+=[^}]*)\}(?P<span>.*?)\{/\}", re.S)


def _escape(s: str) -> str:
    return s.replace("\\", "＼").replace("{", "｛").replace("}", "｝")


def parse_markup(text: str, highlight: dict[str, Any], per_char: bool = False) -> list[Run]:
    segments: list[tuple[str, dict[str, Any]]] = []
    pos = 0
    for m in _TOKEN.finditer(text):
        if m.start() > pos:
            segments.append((text[pos:m.start()], {}))
        if m.group("hl") is not None:
            segments.append((m.group("hl"), {"color": highlight.get("color"),
                                             "scale": highlight.get("scale", 1.0),
                                             "outline_color": highlight.get("outline_color")}))
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
            runs.append(Run(out, word, attrs.get("color"), attrs.get("scale", 1.0), attrs.get("outline_color")))
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
            b=-1 if st.get("bold", True) else 0, sp=st.get("spacing", 0), bs=border_style,
            o=outline, s=shadow, a=a, ml=st["margin_h"] * k, mr=st["margin_h"] * k, mv=st["margin_v"] * k)

    @staticmethod
    def style_name(name: str) -> str:
        # libass は "Default" という名前を特別扱いするので接頭辞を付ける
        return f"T_{name}"

    @staticmethod
    def _has_outer(st: dict[str, Any]) -> bool:
        return bool(st.get("outer_outline_color")) and st.get("outer_outline", 0) > 0 and not st.get("box")

    # -- events -------------------------------------------------------------
    def add(self, telop: dict[str, Any], cut_start: float, cut_dur: float) -> None:
        st = self.styles[telop["style"]]
        anim = telop.get("anim") or st.get("anim", "none")
        if anim not in ANIMS:
            raise ValueError(f"未知のテロップアニメ: {anim} (使えるもの: {sorted(ANIMS)})")
        t0 = float(telop.get("start", 0.0))
        t1 = telop.get("end")
        t1 = cut_dur if t1 is None else (cut_dur + float(t1) if float(t1) < 0 else float(t1))
        t1 = min(t1, cut_dur)
        if t1 - t0 < 0.05:
            return
        dur_ms = (t1 - t0) * 1000
        runs = parse_markup(str(telop["text"]), st.get("highlight", {}), per_char=(anim == "typewriter"))
        n_words = max((r.word for r in runs), default=0) + 1
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
        if anim == "pop":
            tags += "\\fad(0,120)"
        elif anim == "fade":
            tags += "\\fad(200,150)"
        elif anim == "slide_up":
            x, y = (telop["pos"][0] * self.W, telop["pos"][1] * self.H) if "pos" in telop else self._anchor(st)
            off = 50 * self.k
            tags += f"\\move({x:.0f},{y + off:.0f},{x:.0f},{y:.0f},0,220)\\fad(160,120)"
        elif anim in ("words", "karaoke", "typewriter"):
            tags += "\\fad(0,120)"
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
        if anim == "pop":
            tags.append(f"\\fscx0\\fscy0\\t(0,110,\\fscx{s * 1.18:.0f}\\fscy{s * 1.18:.0f})"
                        f"\\t(110,200,\\fscx{s:.0f}\\fscy{s:.0f})")
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
