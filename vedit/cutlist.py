"""カット表 (JSON / CSV) の読み込みと正規化."""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
from typing import Any

from .media import RenderError

ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5)}

# ---- 組み込みのテロップスタイル -------------------------------------------------
# size / outline 等は「短辺 1080px」基準の px 値。
BUILTIN_STYLES: dict[str, dict[str, Any]] = {
    "default": {
        "font": None, "size": 72, "color": "#FFFFFF", "outline_color": "#000000", "outline": 7,
        "outer_outline_color": None, "outer_outline": 0, "shadow": 3, "shadow_color": "#000000",
        "bold": True, "align": "bottom", "margin_v": 70, "margin_h": 80, "spacing": 1,
        "box": False, "box_color": "#000000", "box_alpha": 0.35,
        "highlight": {"color": "#FFE600", "scale": 1.25},
        "anim": "pop",
    },
    "hook": {
        "size": 140, "color": "#FFE600", "outline_color": "#000000", "outline": 10,
        "outer_outline_color": "#FFFFFF", "outer_outline": 8, "shadow": 6,
        "align": "center", "highlight": {"color": "#FF3B3B", "scale": 1.2},
        "anim": "pop",
    },
    "emphasis": {
        "size": 96, "color": "#FFFFFF", "outline_color": "#D7263D", "outline": 9,
        "outer_outline_color": "#FFFFFF", "outer_outline": 6, "highlight": {"color": "#FFE600", "scale": 1.3},
    },
    "title": {
        "size": 110, "color": "#FFFFFF", "outline_color": "#D7263D", "outline": 9,
        "outer_outline_color": "#FFFFFF", "outer_outline": 6, "align": "center",
        "highlight": {"color": "#FFE600", "scale": 1.3},
    },
    "label": {
        "size": 52, "color": "#FFFFFF", "outline": 0, "shadow": 0, "align": "top_left",
        "margin_v": 40, "margin_h": 48, "box": True, "box_color": "#D7263D", "box_alpha": 0.0,
        "anim": "slide_up",
    },
}

# ---- カットタイプ (プリセット) -----------------------------------------------
# cut.type で指定。カット側に書いた値がプリセットより優先される。
BUILTIN_CUT_TYPES: dict[str, dict[str, Any]] = {
    "normal": {},
    # 冒頭フック: 単色背景 + 画面中央の巨大テロップ + ポップイン + 揺れ + ゆっくりズーム
    "hook": {
        "source": "color:#14141C",
        "duration": 3.0,
        "telop": {"style": "hook", "anim": "pop"},
        "effects": [
            {"type": "flash", "duration": 0.25},
            {"type": "zoom_in", "to": 1.12},
            {"type": "shake", "start": 0.05, "duration": 0.4, "amplitude": 22},
        ],
    },
    # 章タイトル / CTA 用
    "title": {
        "source": "color:#101010",
        "duration": 2.5,
        "telop": {"style": "title", "anim": "pop"},
        "effects": [{"type": "fade_in", "duration": 0.2}, {"type": "fade_out", "duration": 0.3}],
    },
}

DEFAULT_PROJECT: dict[str, Any] = {
    "title": "untitled",
    "aspect": "16:9",
    "resolution": 1080,          # 短辺の px
    "fps": 30,
    "target_duration": None,     # 例: 90 (超過/不足を警告するだけ)
    "font": "IPAGothic",
    "fonts_dir": None,           # 独自フォントを置くフォルダ (任意)
    "output": "out/output.mp4",
    "video": {"crf": 20, "preset": "medium"},
    "audio": {"loudnorm": True, "target_lufs": -14.0},
    "bgm": None,
    "defaults": {
        "fit": "cover",
        "jumpcut": {"noise": "-35dB", "min_silence": 0.35, "padding": 0.08, "min_keep": 0.12},
        "narration_tail": 0.35,
    },
    "styles": {},
    "cut_types": {},
    "overrides": {},
}

DEFAULT_BGM = {"file": None, "volume": 0.35, "fade_in": 1.0, "fade_out": 2.0,
               "ducking": {"enabled": True, "threshold": 0.04, "ratio": 6, "attack": 15,
                           "release": 350, "makeup": 1.0}}


def deep_merge(base: Any, over: Any) -> Any:
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = deep_merge(base.get(k), v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(over)


# ---- CSV ------------------------------------------------------------------------
def _scalar(v: str) -> Any:
    v = v.strip()
    if v == "":
        return None
    low = v.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def _kv(text: str) -> dict[str, Any]:
    """'a=1,b=x' -> {'a': 1, 'b': 'x'}"""
    out: dict[str, Any] = {}
    for part in text.split(","):
        if not part.strip():
            continue
        if "=" not in part:
            raise RenderError(f"CSV の key=value 形式が不正です: {part!r}")
        k, v = part.split("=", 1)
        out[k.strip()] = _scalar(v)
    return out


def _maybe_json(cell: str) -> Any:
    s = cell.strip()
    if s.startswith(("{", "[")):
        return json.loads(s)
    return None


def parse_effects_cell(cell: str) -> list[dict[str, Any]]:
    """'zoom_in:to=1.2; shake:start=1,duration=0.3' -> [{type:zoom_in,to:1.2}, ...]"""
    if (j := _maybe_json(cell)) is not None:
        return j
    effects = []
    for item in cell.split(";"):
        item = item.strip()
        if not item:
            continue
        name, _, params = item.partition(":")
        eff = {"type": name.strip()}
        eff.update(_kv(params))
        effects.append(eff)
    return effects


def parse_se_cell(cell: str) -> list[dict[str, Any]]:
    """'assets/se_pop.wav@0.5; assets/se_whoosh.wav@0' """
    if (j := _maybe_json(cell)) is not None:
        return j
    out = []
    for item in cell.split(";"):
        item = item.strip()
        if not item:
            continue
        file, _, rest = item.partition("@")
        at, _, vol = rest.partition("*")
        se = {"file": file.strip(), "at": float(at or 0)}
        if vol:
            se["volume"] = float(vol)
        out.append(se)
    return out


def load_csv(path: Path) -> dict[str, Any]:
    cuts = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row_no, row in enumerate(csv.DictReader(f), start=2):
            row = {k.strip(): (v or "") for k, v in row.items() if k}
            if not any(v.strip() for v in row.values()) or row.get("id", "").startswith("#"):
                continue
            cut: dict[str, Any] = {}
            for key in ("id", "type", "source", "fit"):
                if row.get(key, "").strip():
                    cut[key] = row[key].strip()
            for key in ("in", "out", "duration", "source_volume"):
                if (v := _scalar(row.get(key, ""))) is not None:
                    cut[key] = v
            if row.get("telop", "").strip():
                telop = {"text": row["telop"].replace("\\n", "\n")}
                for src_key, dst in (("telop_style", "style"), ("telop_anim", "anim"),
                                     ("telop_start", "start"), ("telop_end", "end")):
                    if (v := _scalar(row.get(src_key, ""))) is not None:
                        telop[dst] = v
                cut["telop"] = telop
            if row.get("effects", "").strip():
                cut["effects"] = parse_effects_cell(row["effects"])
            if row.get("pip", "").strip():
                cut["pip"] = _maybe_json(row["pip"]) or _kv(row["pip"])
            if row.get("narration", "").strip():
                narr: dict[str, Any] = {"file": row["narration"].strip()}
                if (v := _scalar(row.get("narration_offset", ""))) is not None:
                    narr["offset"] = v
                cut["narration"] = narr
            if (jc := row.get("jumpcut", "").strip()):
                v = _scalar(jc)
                cut["jumpcut"] = v if isinstance(v, bool) else (_maybe_json(jc) or _kv(jc))
            if row.get("se", "").strip():
                cut["se"] = parse_se_cell(row["se"])
            if not cut.get("id"):
                cut["id"] = f"row{row_no}"
            cuts.append(cut)
    return {"cuts": cuts}


# ---- 読み込み & 正規化 -----------------------------------------------------------
def load_cutlist(path: Path, project_path: Path | None = None,
                 cli_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        doc = load_csv(path)
        if project_path is None:
            sidecar = path.with_suffix(".project.json")
            project_path = sidecar if sidecar.exists() else None
        if project_path:
            doc["project"] = json.loads(Path(project_path).read_text(encoding="utf-8")).get("project", {})
    else:
        doc = json.loads(path.read_text(encoding="utf-8"))
    base_dir = path.parent.resolve()

    project = deep_merge(DEFAULT_PROJECT, doc.get("project", {}))
    if cli_overrides:
        project = deep_merge(project, cli_overrides)
    cuts = doc.get("cuts", [])

    # アスペクト比ごとの上書き (例: overrides["9:16"].styles.default.size = 64)
    aspect = project["aspect"]
    if aspect not in ASPECTS:
        raise RenderError(f"未対応の aspect: {aspect} (対応: {list(ASPECTS)})")
    ov = project.get("overrides", {}).get(aspect)
    if ov:
        project = deep_merge(project, {k: v for k, v in ov.items() if k != "cuts"})
        for cut in cuts:
            if cut.get("id") in ov.get("cuts", {}):
                cut.update(deep_merge(cut, ov["cuts"][cut["id"]]))

    # 解像度
    aw, ah = ASPECTS[aspect]
    short = int(project["resolution"])
    if aw >= ah:
        h = short; w = round(short * aw / ah / 2) * 2
    else:
        w = short; h = round(short * ah / aw / 2) * 2
    project["width"], project["height"] = w, h

    # スタイル (default を継承)
    styles = deep_merge(BUILTIN_STYLES, project.get("styles", {}))
    base = styles["default"]
    project["styles"] = {name: (deep_merge(base, s) if name != "default" else base) for name, s in styles.items()}

    if project.get("bgm"):
        project["bgm"] = deep_merge(DEFAULT_BGM, project["bgm"])

    cut_types = deep_merge(BUILTIN_CUT_TYPES, project.get("cut_types", {}))
    normalized = [normalize_cut(c, i, project, cut_types) for i, c in enumerate(cuts)]
    if not normalized:
        raise RenderError("カットが 1 つもありません")

    project["_base_dir"] = str(base_dir)
    return {"project": project, "cuts": normalized, "_base_dir": base_dir}


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def normalize_cut(cut: dict[str, Any], idx: int, project: dict[str, Any],
                  cut_types: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ctype = cut.get("type", "normal")
    if ctype not in cut_types:
        raise RenderError(f"cut[{idx}] 未知の type: {ctype} (定義済み: {list(cut_types)})")
    preset = copy.deepcopy(cut_types[ctype])
    c = dict(preset)
    for k, v in cut.items():
        if k == "telop" and isinstance(v, (str, dict)) and isinstance(preset.get("telop"), dict):
            v = deep_merge(preset["telop"], {"text": v} if isinstance(v, str) else v)
        c[k] = v
    c["id"] = str(c.get("id", f"cut{idx + 1:02d}"))
    c["type"] = ctype
    if "source" not in c:
        raise RenderError(f"cut '{c['id']}' に source がありません")
    c.setdefault("fit", project["defaults"]["fit"])
    c.setdefault("source_volume", 1.0)

    # telop: str | dict | list[dict]
    telops = []
    for t in _as_list(c.get("telop")):
        if isinstance(t, str):
            t = {"text": t}
        t = dict(t)
        t.setdefault("style", "default")
        if t["style"] not in project["styles"]:
            raise RenderError(f"cut '{c['id']}' 未定義のテロップスタイル: {t['style']}")
        telops.append(t)
    c["telop"] = telops

    c["effects"] = [dict(e) for e in _as_list(c.get("effects"))]
    c["pip"] = [dict(p) for p in _as_list(c.get("pip"))]
    c["se"] = [({"file": s} if isinstance(s, str) else dict(s)) for s in _as_list(c.get("se"))]

    narr = c.get("narration")
    if isinstance(narr, str):
        narr = {"file": narr}
    if narr:
        narr = {"offset": 0.0, "volume": 1.0, **narr}
    c["narration"] = narr

    jc = c.get("jumpcut", False)
    if jc:
        jc = {} if jc is True else dict(jc)
        jc = {**project["defaults"]["jumpcut"], **jc}
    c["jumpcut"] = jc or None
    return c
