"""CLI.

  python -m boatrace fetch 2025-01-01 2026-10-23   # 過去データをキャッシュ
  python -m boatrace train 2025-01-01 2026-10-23   # 学習 + 検証レポート
  python -m boatrace fetch-odds 2026-10-17 2026-10-23   # 確定オッズ取得（公式サイト、低速）
  python -m boatrace backtest-ev 2026-10-17 2026-10-23  # 期待値買い目の検証
  python -m boatrace backfill 2026-08-25 2026-10-23     # 過去日の予想をサイト用に作成（検証データ）
  python -m boatrace predict [2026-10-24]          # 指定日の予測JSONを出力
  python -m boatrace update                        # 定期実行用（前日結果 + 当日予測）
"""
import argparse
from datetime import date

from . import pipeline
from .config import today_jst
from .fetch import fetch_range
from .odds import fetch_final_range


def main():
    ap = argparse.ArgumentParser(prog="boatrace")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fetch", "train", "fetch-odds", "backtest-ev", "backfill"):
        p = sub.add_parser(name)
        p.add_argument("start", type=date.fromisoformat)
        p.add_argument("end", type=date.fromisoformat)
    p = sub.add_parser("predict")
    p.add_argument("date", type=date.fromisoformat, nargs="?")
    sub.add_parser("update")
    sub.add_parser("reattach-odds")
    args = ap.parse_args()

    if args.cmd == "fetch":
        fetch_range(args.start, args.end)
    elif args.cmd == "fetch-odds":
        fetch_final_range(args.start, args.end)
    elif args.cmd == "backfill":
        pipeline.backfill(args.start, args.end)
    elif args.cmd == "backtest-ev":
        pipeline.backtest_ev(args.start, args.end)
    elif args.cmd == "train":
        pipeline.train_all(args.start, args.end)
        pipeline.build_index()
    elif args.cmd == "predict":
        data = pipeline.predict_day(args.date or today_jst())
        pipeline.build_index()
        print(f"{data['date']}: {len(data['races'])} races")
    elif args.cmd == "reattach-odds":
        pipeline.reattach_backfill_odds()
    elif args.cmd == "update":
        pipeline.update()


if __name__ == "__main__":
    main()
