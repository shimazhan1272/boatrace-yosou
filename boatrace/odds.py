"""ボートレース公式サイトからのオッズ取得。

サイトポリシーで大量アクセスが禁止されているため、
  - リクエスト間隔を最低 REQUEST_INTERVAL 秒あける
  - 締切後（確定）のオッズはキャッシュして二度と取りに行かない
  - 取得したオッズそのものは公開サイトに転載しない（推奨買い目のオッズのみ表示）
"""
import json
import re
import threading
import time
from datetime import date, datetime
from itertools import permutations

import requests

from .config import DATA_DIR, JST

ODDS_URL = "https://www.boatrace.jp/owpc/pc/race/{page}?rno={rno}&jcd={jcd:02d}&hd={hd}"
ODDS_DIR = DATA_DIR / "odds"
REQUEST_INTERVAL = 1.5

_session = requests.Session()
_session.headers["User-Agent"] = "Mozilla/5.0 (compatible; boatrace-ai/0.1)"
_lock = threading.Lock()
_last_request = 0.0

_ODDS_RE = re.compile(r'class="oddsPoint[^"]*">([^<]*)<')


def _get(url: str) -> str:
    global _last_request
    with _lock:
        wait = _last_request + REQUEST_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            res = _session.get(url, timeout=30)
        finally:
            _last_request = time.monotonic()
    res.raise_for_status()
    return res.text


def _to_float(s: str):
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None  # 欠場・発売なし等


def _combos(n: int):
    """表の並び順（行優先、列=1着艇）に対応する組番を返す。"""
    per_col = {f: sorted(p for p in permutations([b for b in range(1, 7) if b != f], n - 1)) for f in range(1, 7)}
    rows = len(per_col[1])
    return ["-".join(map(str, (f,) + per_col[f][r])) for r in range(rows) for f in range(1, 7)]


TRIFECTA_COMBOS = _combos(3)  # 120通り
EXACTA_COMBOS = _combos(2)    # 30通り


def parse_odds(html: str, combos: list[str]) -> dict[str, float]:
    values = _ODDS_RE.findall(html)[: len(combos)]
    if len(values) < len(combos):
        return {}
    return {c: v for c, v in zip(combos, map(_to_float, values)) if v}


def fetch_odds(d: date, stadium: int, race_number: int, final: bool, cached_only: bool = False) -> dict:
    """3連単・2連単オッズ。final=True（締切後）なら確定オッズとしてキャッシュする。"""
    path = ODDS_DIR / f"{d:%Y%m%d}" / f"{stadium:02d}_{race_number:02d}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if cached_only:
        return {"trifecta": {}, "exacta": {}}

    hd = f"{d:%Y%m%d}"
    tri = parse_odds(_get(ODDS_URL.format(page="odds3t", rno=race_number, jcd=stadium, hd=hd)), TRIFECTA_COMBOS)
    ex = parse_odds(_get(ODDS_URL.format(page="odds2tf", rno=race_number, jcd=stadium, hd=hd)), EXACTA_COMBOS)
    data = {
        "fetched_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "final": final,
        "trifecta": tri,
        "exacta": ex,
    }
    if final and tri:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    return data


def fetch_final_range(start: date, end: date):
    """検証用: 期間内の全レースの確定オッズを（ゆっくり）取得してキャッシュする。"""
    from .fetch import date_range, fetch_day

    for d in date_range(start, end):
        races = [(r["race_stadium_number"], r["race_number"]) for r in fetch_day("results", d)]
        for i, (s, n) in enumerate(races, 1):
            try:
                fetch_odds(d, s, n, final=True)
            except requests.RequestException as e:
                print(f"  {d} {s:02d}-{n:02d} failed: {e}")
            if i % 24 == 0 or i == len(races):
                print(f"  {d}: {i}/{len(races)}", flush=True)
