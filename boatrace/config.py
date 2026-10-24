"""共通設定。"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"          # APIレスポンスのキャッシュ（git管理外）
MODEL_DIR = ROOT / "models"         # 学習済みモデル（git管理）
SITE_DIR = ROOT / "docs"            # GitHub Pages で公開するディレクトリ
SITE_DATA_DIR = SITE_DIR / "data"

API_BASE = "https://boatraceopenapi.github.io"
ENDPOINTS = ("programs", "previews", "results")

JST = timezone(timedelta(hours=9))


def today_jst():
    return datetime.now(JST).date()


STADIUMS = {
    1: "桐生", 2: "戸田", 3: "江戸川", 4: "平和島", 5: "多摩川", 6: "浜名湖",
    7: "蒲郡", 8: "常滑", 9: "津", 10: "三国", 11: "びわこ", 12: "住之江",
    13: "尼崎", 14: "鳴門", 15: "丸亀", 16: "児島", 17: "宮島", 18: "徳山",
    19: "下関", 20: "若松", 21: "芦屋", 22: "福岡", 23: "唐津", 24: "大村",
}

# racer_class_number: 1=A1, 2=A2, 3=B1, 4=B2
RACER_CLASSES = {1: "A1", 2: "A2", 3: "B1", 4: "B2"}
