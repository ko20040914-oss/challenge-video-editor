"""「人気YouTuber編集の型」仕様 (2026-11-01) の数値と、テロッププリセット A/B/C.

数値はすべて仕様ファイルから取っています。仕様が範囲 (例 80〜120ms) で示している値は、
範囲内の値 (原則として中央付近) を採用し、コメントに元の範囲を併記しています。
"""
from __future__ import annotations

import functools
import logging
import subprocess
from typing import Any

log = logging.getLogger("vedit")

# ---- 色 (仕様「テロップの型 / 色」) --------------------------------------------
YELLOW = "#FFE600"      # 強調 (1文に1〜2語だけ)
RED = "#FF2E3D"         # 警告・否定
CYAN = "#66D9FF"        # 結果 (水色)
GREEN = "#3DDC84"       # 結果 (緑)
LIGHT_BLUE = "#BFE9FF"  # ツッコミ (薄水色)
WHITE, BLACK = "#FFFFFF", "#000000"

# ---- アニメーション (仕様「アニメーション」) ----------------------------------
POP_FROM = 0.90         # ポップイン 90%→100%
POP_MS = 100            # 80〜120ms
SLIDE_PX = 25           # スライドアップ 20〜30px
SLIDE_MS = 125          # 100〜150ms
BOUNCE = 1.15           # 強調語の跳ね 115%
BOUNCE_MS = 100         # 80〜120ms
FADE_OUT_MS = 80        # 消え際フェード 60〜100ms
SPACING_PCT = 0.04      # 字間やや広め +2〜6%

# ---- 文字量の上限 (仕様「フォント」/「プリセット」) -----------------------------
LINE_MAX_CHARS = 16     # 1行12〜16文字上限
MAX_LINES = 2           # 最大2行
MAX_COLORS = 2          # 1画面2色まで
EMPH_MAX_WORDS = 2      # 強調は1文1〜2語まで
TSUKKOMI_MAX_PER_MIN = 6  # ツッコミ 1分3〜6回
SHARE_TARGET = {"A": 0.70, "B": 0.20, "C": 0.10}  # 配分の目安 標準70%/強調20%/ツッコミ10%

# ---- パンチズーム (仕様「演出」) --------------------------------------------
PUNCH_TO = 1.12         # 100→108〜115%
PUNCH_IN_SEC = 0.20     # 0.15〜0.25秒で寄せ
PUNCH_HOLD_SEC = 0.40   # 0.3〜0.5秒保持
PUNCH_MAX_PER_MIN = 6   # 1分に3〜6回まで

# ---- ジャンプカット (仕様「カットのリズム」) ----------------------------------
JUMPCUT_MIN_SILENCE = 0.4   # 0.4秒以上の無音は原則カット
JUMPCUT_PADDING = 0.10      # 前後に残す余白。合計0.2秒 = 呼吸0.15〜0.30秒を残す
PAUSES = {                  # 残す「間」(ホワイトリスト)
    "before_punchline": {"default": 0.45, "range": (0.3, 0.6), "label": "オチ前"},
    "after_punchline": {"default": 1.0, "range": (0.8, 1.2), "label": "オチ後"},
    "scene_change": {"default": 0.75, "range": (0.5, 1.0), "label": "場面転換"},
}

# ---- BGM ダッキング (仕様「音」) ---------------------------------------------
BGM_VOLUME = 0.40           # 間では0.35〜0.45倍
BGM_TALK_RANGE = (0.12, 0.20)  # 会話時0.12〜0.20倍 (検証の目標値)
DUCKING = {"threshold": 0.05, "ratio": 4,
           "attack": 12.5,      # 5〜20ms
           "release": 300}      # 200〜400ms

# ---- フォント (仕様「フリー置き換え」とフォールバック順) -------------------------
# 上から順に fc-list で探し、無ければ project.font (既定 IPAGothic, 擬似太字) を使う。
FONT_A = ["Source Han Sans JP Heavy", "源ノ角ゴシック JP Heavy", "Noto Sans CJK JP Black",
          "Noto Sans JP Black", "M PLUS Rounded 1c Black"]
FONT_B = ["Source Han Sans JP Black", "源ノ角ゴシック JP Black", "Noto Sans CJK JP Black",
          "Noto Sans JP Black", "M PLUS Rounded 1c Black"]
FONT_C = ["M PLUS Rounded 1c ExtraBold", "M PLUS Rounded 1c Black", "Noto Sans JP Black",
          "Source Han Sans JP Heavy", "Noto Sans CJK JP Black"]

_ANIM = {"pop_from": POP_FROM, "pop_ms": POP_MS, "pop_overshoot": None, "slide_px": SLIDE_PX,
         "slide_ms": SLIDE_MS, "bounce": BOUNCE, "bounce_ms": BOUNCE_MS, "fade_out_ms": FADE_OUT_MS}
_COMMON = {"color": WHITE, "outline_color": BLACK, "outer_outline": 0, "outer_outline_color": None,
           "shadow": 2, "shadow_color": BLACK, "bold": True, "align": "bottom", "margin_v": 110,
           "spacing_pct": SPACING_PCT, "box": False, "warn_color": RED,
           "line_max_chars": LINE_MAX_CHARS, "max_lines": MAX_LINES, "max_colors": MAX_COLORS,
           "max_emph_words": EMPH_MAX_WORDS, **_ANIM}

# プリセット (= テロップスタイル)。cut の telop で "preset": "A" (または "style": "A") と指定する。
PRESET_STYLES: dict[str, dict[str, Any]] = {
    # A 標準: 源ノ角 Heavy, 80, 白, 縁黒5px, 影2px, 下部中央 MarginV110, ポップイン+スライドアップ, SEポップ
    "A": {**_COMMON, "font": FONT_A, "size": 80, "outline": 5, "anim": "pop_slide", "se": "pop",
          "preset_group": "A", "highlight": {"color": YELLOW, "scale": 1.0, "bounce": BOUNCE}},
    # B 強調: 源ノ角 Black, 80, 黄 #FFE600, 縁黒5px, 影2px, 跳ね115% + パンチズーム同期, 1文1〜2語まで
    "B": {**_COMMON, "font": FONT_B, "size": 80, "color": YELLOW, "outline": 5, "anim": "bounce",
          "punch": True, "preset_group": "B", "check_words": True,
          "highlight": {"color": YELLOW, "scale": 1.0, "bounce": BOUNCE}},
    # B の警告版: 赤 #FF2E3D
    "B_warn": {**_COMMON, "font": FONT_B, "size": 80, "color": RED, "outline": 5, "anim": "bounce",
               "punch": True, "preset_group": "B", "check_words": True,
               "highlight": {"color": RED, "scale": 1.0, "bounce": BOUNCE}},
    # C ツッコミ: M PLUS Rounded 1c ExtraBold, 56, 白 or #BFE9FF, 縁黒3px, 影2px, ポップインのみ, カッコ付き
    "C": {**_COMMON, "font": FONT_C, "size": 56, "outline": 3, "anim": "pop", "brackets": "（）",
          "preset_group": "C", "highlight": {"color": LIGHT_BLUE, "scale": 1.0}},
    "C_light": {**_COMMON, "font": FONT_C, "size": 56, "color": LIGHT_BLUE, "outline": 3, "anim": "pop",
                "brackets": "（）", "preset_group": "C", "highlight": {"color": WHITE, "scale": 1.0}},
}
PRESET_ALIASES = {"標準": "A", "強調": "B", "強調_警告": "B_warn", "ツッコミ": "C", "ツッコミ_水色": "C_light"}


@functools.lru_cache(maxsize=1)
def installed_families() -> frozenset[str]:
    try:
        out = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True).stdout
    except FileNotFoundError:
        return frozenset()
    names = set()
    for line in out.splitlines():
        for name in line.split(","):
            names.add(name.strip().replace("\\-", "-").lower())
    return frozenset(names)


def resolve_font(candidates: str | list[str] | None, fallback: str) -> tuple[str, bool]:
    """候補の中で最初にインストールされているフォント名を返す. (名前, 候補が見つかったか)"""
    if not candidates:
        return fallback, True
    if isinstance(candidates, str):
        candidates = [candidates]
    fams = installed_families()
    for c in candidates:
        if c.lower() in fams:
            return c, True
    return fallback, False
