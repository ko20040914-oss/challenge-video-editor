"""プリセット A/B/C・間のホワイトリスト・パンチズーム同期・ダッキング既定値のテスト (ffmpeg 不要)."""
import json
import tempfile
import unittest
from pathlib import Path

from vedit import presets as P
from vedit.cutlist import DEFAULT_BGM, load_cutlist
from vedit.segment import apply_pause_whitelist, keep_intervals, punch_factor
from vedit.telop import AssBuilder, ass_color, parse_markup


def load(cuts, project=None):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.json"
        p.write_text(json.dumps({"project": project or {}, "cuts": cuts}, ensure_ascii=False), encoding="utf-8")
        return load_cutlist(p)


class TestPresetValues(unittest.TestCase):
    def test_spec_numbers(self):
        a, b, c = P.PRESET_STYLES["A"], P.PRESET_STYLES["B"], P.PRESET_STYLES["C"]
        self.assertEqual((a["size"], a["color"], a["outline"], a["shadow"], a["margin_v"]),
                         (80, "#FFFFFF", 5, 2, 110))
        self.assertEqual((b["size"], b["color"], b["outline"], b["shadow"], b["bounce"]), (80, "#FFE600", 5, 2, 1.15))
        self.assertEqual(P.PRESET_STYLES["B_warn"]["color"], "#FF2E3D")
        self.assertEqual((c["size"], c["outline"], c["shadow"], c["anim"]), (56, 3, 2, "pop"))
        self.assertEqual(P.PRESET_STYLES["C_light"]["color"], "#BFE9FF")

    def test_ranges(self):
        self.assertTrue(80 <= P.POP_MS <= 120 and P.POP_FROM == 0.90)
        self.assertTrue(20 <= P.SLIDE_PX <= 30 and 100 <= P.SLIDE_MS <= 150)
        self.assertTrue(80 <= P.BOUNCE_MS <= 120 and 60 <= P.FADE_OUT_MS <= 100)
        self.assertTrue(0.02 <= P.SPACING_PCT <= 0.06)
        self.assertTrue(1.08 <= P.PUNCH_TO <= 1.15 and 0.15 <= P.PUNCH_IN_SEC <= 0.25 and 0.3 <= P.PUNCH_HOLD_SEC <= 0.5)
        for v in P.PAUSES.values():
            lo, hi = v["range"]
            self.assertTrue(lo <= v["default"] <= hi)

    def test_bgr_colors(self):
        self.assertEqual(ass_color("#FFE600"), "&H0000E6FF")
        self.assertEqual(ass_color("#FF2E3D"), "&H003D2EFF")
        self.assertEqual(ass_color("#BFE9FF"), "&H00FFE9BF")
        self.assertEqual(ass_color("#66D9FF"), "&H00FFD966")


class TestPresetUsage(unittest.TestCase):
    def test_preset_alias_brackets_and_punch(self):
        doc = load([{"id": "x", "source": "color:#000000", "duration": 3,
                     "telop": [{"text": "ふつう", "preset": "標準"},
                               {"text": "ここ大事", "preset": "B", "start": 1.0},
                               {"text": "なんでやねん", "preset": "C"}]}])
        cut = doc["cuts"][0]
        self.assertEqual([t["style"] for t in cut["telop"]], ["A", "B", "C"])
        self.assertEqual(cut["telop"][2]["text"], "（なんでやねん）")
        punches = [e for e in cut["effects"] if e["type"] == "punch_zoom"]
        self.assertEqual(len(punches), 1)
        self.assertEqual(punches[0]["start"], 1.0)

    def test_font_swappable(self):
        doc = load([{"id": "x", "source": "color:#000000", "telop": {"text": "a", "preset": "A"}}],
                   {"styles": {"A": {"font": "MyFont"}}, "font": "Fallback"})
        self.assertIn(doc["project"]["styles"]["A"]["font"], ("MyFont", "Fallback"))

    def test_ass_tags(self):
        doc = load([{"id": "x", "source": "color:#000000", "duration": 2,
                     "telop": {"text": "これは**大事**", "preset": "A"}}])
        ab = AssBuilder(doc["project"])
        ab.add(doc["cuts"][0]["telop"][0], 0, 2)
        text = ab.render()
        self.assertIn("\\fscx90\\fscy90\\t(0,100,\\fscx100\\fscy100)", text)     # ポップイン 90→100% 100ms
        self.assertIn("\\t(0,50,\\fscx115\\fscy115)\\t(50,100,\\fscx100\\fscy100)", text)  # 跳ね 115%
        self.assertIn("\\move(960,995,960,970,0,125)\\fad(0,80)", text)          # スライド 25px/125ms, 消え 80ms
        self.assertIn("Style: T_A,", text)
        self.assertIn(",5.0,2.0,2,80,80,110,1", text)                               # 縁5 影2 下部中央 MarginV110

    def test_warn_markup(self):
        runs = parse_markup("それは!!ダメ!!", {"color": "#FFE600", "bounce": 1.15}, warn_color="#FF2E3D")
        self.assertEqual(runs[-1].color, "#FF2E3D")
        self.assertTrue(runs[-1].emph)


class TestPauseWhitelist(unittest.TestCase):
    def test_keep_pause_lengths(self):
        sil = [(1.0, 3.0), (5.0, 7.0)]
        new, rep = apply_pause_whitelist(sil, [{"type": "after_punchline", "at": 2.0}], padding=0.1)
        keeps = keep_intervals(new, 10.0, 0.1, 0.12)
        gaps = [keeps[i + 1][0] - keeps[i][1] for i in range(len(keeps) - 1)]
        # オチ後の間 1.0秒残し → 削除区間は 2.0 - 1.0 = 1.0秒、もう一方の無音は 2.0 - 0.2 = 1.8秒削除
        self.assertAlmostEqual(gaps[0], 1.0, places=6)
        self.assertAlmostEqual(gaps[1], 1.8, places=6)
        self.assertEqual(rep[0]["kept"], 1.0)

    def test_short_silence_kept_whole(self):
        new, _ = apply_pause_whitelist([(1.0, 1.4)], [{"type": "before_punchline", "at": 1.2}], padding=0.1)
        self.assertEqual(new, [])

    def test_default_jumpcut_values(self):
        doc = load([{"id": "x", "source": "color:#000000", "jumpcut": True}])
        jc = doc["cuts"][0]["jumpcut"]
        self.assertEqual((jc["min_silence"], jc["padding"]), (0.4, 0.1))


class TestPunchAndDucking(unittest.TestCase):
    def test_punch_expr(self):
        expr = punch_factor({"start": 1.0}, 30)
        self.assertIn("1.12", expr)
        self.assertIn("lt(on/30,1.6)", expr)   # 1.0 + 0.2 寄せ + 0.4 保持 で戻る

    def test_ducking_defaults(self):
        d = DEFAULT_BGM["ducking"]
        self.assertEqual((d["threshold"], d["ratio"]), (0.05, 4))
        self.assertTrue(5 <= d["attack"] <= 20 and 200 <= d["release"] <= 400)
        self.assertTrue(0.35 <= DEFAULT_BGM["volume"] <= 0.45)


if __name__ == "__main__":
    unittest.main()
