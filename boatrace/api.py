"""更新ボタン用の予想 API（Render で動かす）。

  GET /health                                   起動確認（ページを開いた時点で呼んで起こしておく）
  GET /api/race?date=2026-10-25&stadium=17&race=4

呼ばれたレースの直前情報（とオッズ）をその場で公式サイトから取り、
GitHub Actions が公開している当日の context（過去成績の集計）と合わせて予想し直す。
公式サイトへの負荷を抑えるため、同じレースの取得結果は短時間キャッシュする。
"""
import os
import threading
import time
from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import beforeinfo, model, odds, strategy
from .config import JST, today_jst
from .context import read_context
from .features import add_features, build_frame, has_preview
from .fetch import fetch_day
from .pipeline import _race_prediction

CONTEXT_BASE_URL = os.environ.get(
    "CONTEXT_BASE_URL", "https://shimazhan1272.github.io/boatrace-yosou/data/context")
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS", "https://shimazhan1272.github.io,http://localhost:8765").split(",")

BEFOREINFO_TTL = 60   # 展示前（未取得）の再確認間隔。取得できたら以降は再取得しない
ODDS_TTL = 60
PROGRAMS_TTL = 30 * 60

app = FastAPI(title="boatrace-yosou API")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET"], allow_headers=["*"])

_lock = threading.Lock()
_boosters = {}
_context = {}    # date -> (racers, stadiums)
_programs = {}   # date -> (fetched, list)
_before = {}     # (date, stadium, race) -> (fetched, data|None)
_odds = {}       # (date, stadium, race) -> (fetched, data)


def _booster(kind):
    if kind not in _boosters:
        _boosters[kind] = model.load(kind)
    return _boosters[kind]


def _get_context(d: date):
    if d not in _context:
        base = f"{CONTEXT_BASE_URL}/{d:%Y%m%d}"
        try:
            _context.clear()  # 前日分は捨てる
            _context[d] = read_context(f"{base}/racers.csv.gz?t={time.time():.0f}",
                                       f"{base}/stadiums.csv.gz?t={time.time():.0f}")
        except Exception:
            raise HTTPException(503, "本日の過去データを準備中です（朝の定期更新後に利用できます）")
    return _context[d]


def _program(d: date, stadium: int, race: int):
    hit = _programs.get(d)
    if not hit or time.time() - hit[0] > PROGRAMS_TTL:
        hit = _programs[d] = (time.time(), fetch_day("programs", d, force=True))
    return next((p for p in hit[1] if p["race_stadium_number"] == stadium and p["race_number"] == race), None)


def _beforeinfo(d, stadium, race):
    key = (d, stadium, race)
    hit = _before.get(key)
    if hit and (hit[1] is not None or time.time() - hit[0] < BEFOREINFO_TTL):
        return hit[1]
    data = beforeinfo.fetch_beforeinfo(d, stadium, race)
    _before[key] = (time.time(), data)
    return data


def _live_odds(d, stadium, race):
    key = (d, stadium, race)
    hit = _odds.get(key)
    if hit and time.time() - hit[0] < ODDS_TTL:
        return hit[1]
    data = odds.fetch_odds(d, stadium, race, final=False)
    _odds[key] = (time.time(), data)
    return data


@app.get("/health")
def health():
    return {"ok": True, "time": datetime.now(JST).isoformat(timespec="seconds")}


@app.get("/api/race")
def race(date_: date = Query(alias="date"), stadium: int = Query(ge=1, le=24), race: int = Query(ge=1, le=12)):
    if date_ != today_jst():
        raise HTTPException(400, "更新できるのは当日のレースのみです")
    with _lock:  # 公式サイトへのアクセスと計算は1件ずつ
        program = _program(date_, stadium, race)
        if not program:
            raise HTTPException(404, "該当レースの出走表がありません")
        rh, sh = _get_context(date_)
        try:
            preview = _beforeinfo(date_, stadium, race)
        except Exception:
            raise HTTPException(502, "公式サイトから直前情報を取得できませんでした")

        racers = {b["racer_number"] for b in program["boats"]}
        df = build_frame([program], [preview] if preview else [])
        df = add_features(df, rh[rh["racer_number"].isin(racers)], sh[sh["stadium"] == stadium])
        kind = "full" if has_preview(df).all() else "base"
        df["p"] = model.win_probs(_booster(kind), df, kind)
        result = _race_prediction(df, kind)

        now = datetime.now(JST)
        closed = datetime.strptime(program["race_closed_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
        if now < closed:
            try:
                book = _live_odds(date_, stadium, race)
                if book["trifecta"]:
                    strategy.attach(result, book, book["fetched_at"])
            except Exception:
                pass  # オッズが取れなくても予想は返す
        result["live"] = {"fetched_at": now.strftime("%Y-%m-%d %H:%M:%S"), "exhibition": preview is not None}
        return result
