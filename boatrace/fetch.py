"""Boatrace Open API からのデータ取得とローカルキャッシュ。

過去日のデータは確定しているので gzip で保存して再利用する。
当日・前日分は更新される可能性があるため毎回取り直す。
"""
import gzip
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import requests

from .config import API_BASE, ENDPOINTS, RAW_DIR, today_jst

_session = requests.Session()
_session.headers["User-Agent"] = "boatrace-ai/0.1 (+https://github.com/)"


def _cache_path(endpoint: str, d: date):
    return RAW_DIR / endpoint / f"{d:%Y%m%d}.json.gz"


def fetch_day(endpoint: str, d: date, force: bool = False) -> list[dict]:
    """1日分のレコード一覧を返す。データが無い日は空リスト。"""
    path = _cache_path(endpoint, d)
    volatile = d >= today_jst() - timedelta(days=1)
    if path.exists() and not (force or volatile):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)[endpoint]

    url = f"{API_BASE}/{endpoint}/v2/{d:%Y}/{d:%Y%m%d}.json"
    for attempt in range(3):
        try:
            res = _session.get(url, timeout=30)
            break
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    if res.status_code == 404:
        return []
    res.raise_for_status()
    payload = res.json()

    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return payload.get(endpoint, [])


def date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def fetch_range(start: date, end: date, endpoints=ENDPOINTS, workers: int = 6):
    """期間内の全データをキャッシュに取り込む。"""
    jobs = [(e, d) for d in date_range(start, end) for e in endpoints]
    done = 0
    with ThreadPoolExecutor(workers) as pool:
        for _ in pool.map(lambda job: fetch_day(*job), jobs):
            done += 1
            if done % 100 == 0 or done == len(jobs):
                print(f"  fetched {done}/{len(jobs)}", flush=True)


def load_range(start: date, end: date) -> dict[str, list[dict]]:
    """キャッシュ済みの期間データを endpoint ごとに連結して返す。"""
    out = {e: [] for e in ENDPOINTS}
    for d in date_range(start, end):
        for e in ENDPOINTS:
            out[e].extend(fetch_day(e, d))
    return out
