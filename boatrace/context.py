"""API サーバー（Render）用の「その日の予想に必要な過去データ」ファイル。

Render の無料枠はメモリが小さく、しばらく使われないと停止するので、
1年分の生データは持たせない。GitHub Actions がその日に出走する選手の過去成績と
場×コースの日別合計だけを集計して公開し、API はそれを読むだけにする。
"""
import shutil
from datetime import date, timedelta

import pandas as pd

from .config import DATA_DIR, SITE_DATA_DIR
from .features import HISTORY_DAYS, STADIUM_COLS, history_frame, stadium_daily
from .fetch import fetch_day, load_range

RACER_COLS = ["one", "win", "top3", "place_pts", "st_n", "st_sum", "st_sq", "flying"]
CONTEXT_DIR = SITE_DATA_DIR / "context"


def publish_context(d: date) -> bool:
    """当日の context を公開ディレクトリに置く（定期実行用）。

    中身は前日までのデータなので1日1回作れば良い。作ったものは data/context に残し
    （Actions のキャッシュ対象）、以降の実行ではコピーするだけにする。
    """
    name = f"{d:%Y%m%d}"
    cache = DATA_DIR / "context" / name
    if not (cache / "stadiums.csv.gz").exists():
        if not build_context(d, cache):
            return False
    out = CONTEXT_DIR / name
    out.mkdir(parents=True, exist_ok=True)
    for f in ("racers.csv.gz", "stadiums.csv.gz"):
        shutil.copyfile(cache / f, out / f)
    return True


def build_context(d: date, out=None) -> bool:
    programs = fetch_day("programs", d)
    if not programs:
        return False
    racers = {b["racer_number"] for r in programs for b in r["boats"]}
    past = load_range(d - timedelta(days=HISTORY_DAYS), d - timedelta(days=1))
    h = history_frame(past["results"], past["previews"])

    rh = (h[h["racer_number"].isin(racers)]
          .groupby(["racer_number", "stadium", "course", "date"], as_index=False)[RACER_COLS].sum())
    sh = stadium_daily(h)

    out = out or CONTEXT_DIR / f"{d:%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)
    rh.to_csv(out / "racers.csv.gz", index=False, float_format="%.4g")
    sh.to_csv(out / "stadiums.csv.gz", index=False, float_format="%.6g")
    print(f"context {d}: racers={len(rh)} rows, stadiums={len(sh)} rows")
    return True


def read_context(racers_src, stadiums_src) -> tuple[pd.DataFrame, pd.DataFrame]:
    """パス/URL/ファイルオブジェクトから読み込む。"""
    rh = pd.read_csv(racers_src, compression="gzip", parse_dates=["date"])
    sh = pd.read_csv(stadiums_src, compression="gzip", parse_dates=["date"])
    return rh, sh


__all__ = ["build_context", "publish_context", "read_context", "RACER_COLS", "STADIUM_COLS"]
