"""ffmpeg を使わない部分の単体テスト:  python3 -m unittest discover -s tests"""
import unittest

from vedit.cutlist import parse_effects_cell, parse_se_cell
from vedit.segment import keep_intervals, select_expr
from vedit.telop import ass_color, ass_time, parse_markup


class TestJumpcut(unittest.TestCase):
    def test_keep_intervals(self):
        keeps = keep_intervals([(0.0, 0.5), (2.0, 3.0)], total=5.0, padding=0.1, min_keep=0.1)
        self.assertEqual(keeps, [(0.4, 2.1), (2.9, 5.0)])

    def test_merges_when_padding_overlaps(self):
        keeps = keep_intervals([(1.0, 1.15)], total=2.0, padding=0.1, min_keep=0.1)
        self.assertEqual(keeps, [(0.0, 2.0)])

    def test_select_expr(self):
        self.assertEqual(select_expr([(0, 1), (2, 3.5)]), "between(t,0.000,1.000)+between(t,2.000,3.500)")


class TestTelop(unittest.TestCase):
    def test_color(self):
        self.assertEqual(ass_color("#FF8000"), "&H000080FF")

    def test_time(self):
        self.assertEqual(ass_time(61.234), "0:01:01.23")

    def test_markup(self):
        runs = parse_markup("無音を|**自動で**|{c=#00FF00,s=1.5}カット{/}", {"color": "#FFE600", "scale": 1.3})
        self.assertEqual([r.text for r in runs], ["無音を", "自動で", "カット"])
        self.assertEqual([r.word for r in runs], [0, 1, 2])
        self.assertEqual(runs[1].color, "#FFE600")
        self.assertEqual(runs[2].scale, 1.5)

    def test_escape_and_newline(self):
        runs = parse_markup("a{b}\nc", {})
        self.assertIn("\\N", "".join(r.text for r in runs))
        self.assertNotIn("{", "".join(r.text for r in runs))


class TestCsvCells(unittest.TestCase):
    def test_effects(self):
        self.assertEqual(parse_effects_cell("zoom_in:to=1.2; shake:start=1,duration=0.3"),
                         [{"type": "zoom_in", "to": 1.2}, {"type": "shake", "start": 1, "duration": 0.3}])

    def test_se(self):
        self.assertEqual(parse_se_cell("a.wav@0.5*0.8; b.wav@2"),
                         [{"file": "a.wav", "at": 0.5, "volume": 0.8}, {"file": "b.wav", "at": 2.0}])


if __name__ == "__main__":
    unittest.main()
