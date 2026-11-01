"""CLI:  python3 -m vedit render examples/sample_30s.json [--aspect 9:16] [--preview]"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .media import RenderError
from .render import render


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vedit", description="カット表から動画を自動編集して書き出す")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="カット表 (JSON/CSV) をレンダリング")
    r.add_argument("cutlist", help="カット表 (.json / .csv)")
    r.add_argument("-o", "--output", help="出力 MP4 (省略時は project.output)")
    r.add_argument("--aspect", choices=["16:9", "9:16", "1:1", "4:5"], help="アスペクト比を上書き")
    r.add_argument("--project", help="CSV 用のプロジェクト設定 JSON (省略時は <csv名>.project.json)")
    r.add_argument("--preview", action="store_true", help="540p・高速設定で試し書き出し")
    r.add_argument("--dry-run", action="store_true", help="レンダリングせずタイムラインと ASS だけ作る")
    r.add_argument("--keep-temp", action="store_true", help="中間ファイルを残す")
    r.add_argument("-v", "--verbose", action="store_true", help="ffmpeg コマンドも表示")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    try:
        report = render(args.cutlist, output=args.output, aspect=args.aspect, preview=args.preview,
                        keep_temp=args.keep_temp, dry_run=args.dry_run, project_path=args.project)
    except RenderError as e:
        logging.getLogger("vedit").error("%s", e)
        print(json.dumps({"status": "failed", "error": str(e).splitlines()[0]}, ensure_ascii=False))
        return 1
    print(json.dumps({k: report[k] for k in ("status", "output", "resolution") if k in report}
                     | {"duration": report.get("duration", report.get("planned_duration"))},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
