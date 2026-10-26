"""更新ボタン用の予想 API（Render で動かす）。

  GET /health                                   起動確認（ページを開いた時点で呼んで起こしておく）
  GET /api/race?date=2026-10-25&stadium=17&race=4

呼ばれたレースの直前情報（とオッズ）をその場で公式サイトから取り、
GitHub Actions が公開している当日の context（過去成績の集計）と合わせて予想し直す。
公式サイトへの負荷を抑えるため、同じレースの取得結果は短時間キャッシュする。
"""
import copy
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

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
FRESH_TTL = 15        # 更新ボタン（fresh=1）のときの間隔。押すたびに取り直すが、同じレースは15秒に1回まで
PROGRAMS_TTL = 30 * 60

app = FastAPI(title="boatrace-yosou API")
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET"], allow_headers=["*"])

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


def _programs_of(d: date) -> list[dict]:
    hit = _programs.get(d)
    if not hit or time.time() - hit[0] > PROGRAMS_TTL:
        hit = _programs[d] = (time.time(), fetch_day("programs", d, force=True))
    return hit[1]


def _program(d: date, stadium: int, race: int):
    return next((p for p in _programs_of(d) if p["race_stadium_number"] == stadium and p["race_number"] == race), None)


def _beforeinfo(d, stadium, race, ttl=BEFOREINFO_TTL):
    key = (d, stadium, race)
    hit = _before.get(key)
    if hit and (hit[1] is not None or time.time() - hit[0] < ttl):
        return hit[1]
    data = beforeinfo.fetch_beforeinfo(d, stadium, race)
    _before[key] = (time.time(), data)
    return data


def _live_odds(d, stadium, race, final=False, ttl=ODDS_TTL):
    """締切前は60秒キャッシュ、締切後（締切時オッズ）は取れたら以降は取り直さない。"""
    key = (d, stadium, race)
    hit = _odds.get(key)
    if hit and (hit[1].get("final") and hit[1]["trifecta"]
                or not final and time.time() - hit[0] < ttl):
        return hit[1]
    data = odds.fetch_odds(d, stadium, race, final=final, exacta=False)
    _odds[key] = (time.time(), data)
    return data


@app.get("/health")
def health():
    return {"ok": True, "time": datetime.now(JST).isoformat(timespec="seconds")}


COLLECT_INTERVAL = 60      # 直前情報の自動収集の間隔（秒）
COLLECT_WINDOW_MIN = 25    # 締切この分数前から、展示が出るまで直前情報を取りに行く


def _collect_once():
    """締切が近い全レースの直前情報を集める（展示が出たレースは以降取りに行かない）。"""
    now = datetime.now(JST)
    d = now.date()
    for p in _programs_of(d):
        closed = datetime.strptime(p["race_closed_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
        if now < closed <= now + timedelta(minutes=COLLECT_WINDOW_MIN):
            try:
                _beforeinfo(d, p["race_stadium_number"], p["race_number"])
            except Exception as e:
                print(f"collect failed {p['race_stadium_number']}-{p['race_number']}: {e}")


SELF_URL = os.environ.get("RENDER_EXTERNAL_URL")  # Render が設定する公開 URL
SELF_PING_INTERVAL = 10 * 60


def _collector():
    """GitHub Actions の定期実行は遅れがちなので、直前情報はこのサーバーが毎分集めておく。

    無料枠は外部からのアクセスが15分無いと停止するので、開催時間中は自分の URL に10分おきにアクセスする。
    """
    last_ping = 0.0
    while True:
        try:
            if 7 <= datetime.now(JST).hour < 23:
                _collect_once()
                _live_day(today_jst())  # 画面からの取得がすぐ返るよう、毎分作っておく
                if SELF_URL and time.time() - last_ping > SELF_PING_INTERVAL:
                    requests.get(f"{SELF_URL}/health", timeout=30)
                    last_ping = time.time()
        except Exception as e:
            print(f"collector error: {e}")
        time.sleep(COLLECT_INTERVAL)


@app.on_event("startup")
def _preload():
    """当日の context とモデルを裏で読み込み、直前情報の自動収集を始める。"""
    def load():
        try:
            _booster("base"), _booster("full")
            _get_context(today_jst())
        except Exception:
            pass
    threading.Thread(target=load, daemon=True).start()
    threading.Thread(target=_collector, daemon=True).start()


DAY_TTL = 60
_day_cache = {}   # date -> (computed, payload)
_day_lock = threading.Lock()


def _live_day(d: date) -> dict:
    """集めた直前情報を使って、当日の全レースを予想し直す（1分キャッシュ）。"""
    with _day_lock:
        hit = _day_cache.get(d)
        if hit and time.time() - hit[0] < DAY_TTL:
            return hit[1]
        programs = _programs_of(d)
        rh, sh = _get_context(d)
        previews = [data for (dd, _, _), (_, data) in list(_before.items()) if dd == d and data]
        df = add_features(build_frame(programs, previews), rh, sh)
        df["full"] = has_preview(df)
        df["p"] = model.win_probs(_booster("base"), df, "base")
        sub = df[df["full"]]
        if len(sub):
            df.loc[sub.index, "p"] = model.win_probs(_booster("full"), sub, "full")
        races = [_race_prediction(g, "full" if g["full"].iloc[0] else "base")
                 for _, g in df.groupby("race_id", sort=False)]
        payload = {"date": str(d), "computed_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"), "races": races}
        _day_cache.clear()
        _day_cache[d] = (time.time(), payload)
        return payload


@app.get("/api/day")
def live_day(date_: date = Query(alias="date")):
    """当日の全レースの最新の予想（直前情報が集まったレースは直前情報ありの予想）。"""
    if date_ != today_jst():
        raise HTTPException(400, "当日のみです")
    return _live_day(date_)


@app.get("/api/snapshots")
def snapshots(date_: date = Query(alias="date")):
    """このサーバーが集めた当日の直前情報（取得時刻付き）。定期実行が取り込んで保存する。"""
    previews = [data for (d, _, _), (_, data) in list(_before.items()) if d == date_ and data]
    return {"date": str(date_), "previews": previews}


def _with_courses(preview: dict | None, program: dict, order: list[int]) -> dict:
    """進入コースを手動の並びに置き換えた直前情報（展示前なら進入だけの直前情報）を作る。"""
    course_of = {boat: i + 1 for i, boat in enumerate(order)}
    if preview:
        preview = copy.deepcopy(preview)
        for b in preview["boats"]:
            b["racer_course_number"] = course_of.get(b["racer_boat_number"])
        return preview
    nan = float("nan")
    return {
        "race_date": program["race_date"], "race_stadium_number": program["race_stadium_number"],
        "race_number": program["race_number"],
        "race_wind": nan, "race_wind_direction_number": nan, "race_wave": nan, "race_weather_number": nan,
        "race_temperature": nan, "race_water_temperature": nan,
        "boats": [{"racer_boat_number": boat, "racer_course_number": c, "racer_start_timing": nan,
                   "racer_weight_adjustment": nan, "racer_exhibition_time": nan, "racer_tilt_adjustment": nan}
                  for boat, c in course_of.items()],
    }


@app.get("/api/race")
def race(date_: date = Query(alias="date"), stadium: int = Query(ge=1, le=24), race: int = Query(ge=1, le=12),
         fresh: bool = False, courses: str | None = None):
    """courses: 手動の進入（1コースから順に艇番をカンマ区切り。例 "1,2,3,6,4,5"）。指定時はその並びで予想する。"""
    if date_ != today_jst():
        raise HTTPException(400, "更新できるのは当日のレースのみです")
    program = _program(date_, stadium, race)
    if not program:
        raise HTTPException(404, "該当レースの出走表がありません")
    order = None
    if courses:
        try:
            order = [int(x) for x in courses.split(",")]
        except ValueError:
            order = []
        if sorted(order) != sorted(b["racer_boat_number"] for b in program["boats"]):
            raise HTTPException(400, "進入の指定が不正です")
    now = datetime.now(JST)
    closed = datetime.strptime(program["race_closed_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
    is_closed = now >= closed

    # 直前情報とオッズは並行して取る（公式サイトへのリクエスト開始は odds._get が間隔をあける）
    with ThreadPoolExecutor(2) as pool:
        ttl = FRESH_TTL if fresh else None  # fresh: 更新ボタンから。キャッシュを短くして取り直す
        f_before = pool.submit(_beforeinfo, date_, stadium, race, ttl or BEFOREINFO_TTL)
        f_odds = pool.submit(_live_odds, date_, stadium, race, is_closed, ttl or ODDS_TTL)
        rh, sh = _get_context(date_)
        try:
            preview = f_before.result()
        except Exception:
            raise HTTPException(502, "公式サイトから直前情報を取得できませんでした")
        try:
            book = f_odds.result()
        except Exception:
            book = None  # オッズが取れなくても予想は返す

    racers = {b["racer_number"] for b in program["boats"]}
    used = _with_courses(preview, program, order) if order else preview
    df = build_frame([program], [used] if used else [])
    # 手動の進入: 展示後は直前情報ありのモデルで、展示前は出走表のみのモデルのコース特徴量を手動の並びで計算する
    pre_manual = bool(order) and preview is None
    df = add_features(df, rh[rh["racer_number"].isin(racers)], sh[sh["stadium"] == stadium],
                      base_key="course_est" if pre_manual else "boat")
    kind = "base" if pre_manual else ("full" if order or has_preview(df).all() else "base")
    df["p"] = model.win_probs(_booster(kind), df, kind)
    result = _race_prediction(df, kind)
    if book and book["trifecta"]:
        strategy.attach_odds(result, book, "締切時" if is_closed else book["fetched_at"])
    result["live"] = {"fetched_at": now.strftime("%Y-%m-%d %H:%M:%S"), "exhibition": preview is not None}
    if order:
        result["manual_courses"] = order
    return result
