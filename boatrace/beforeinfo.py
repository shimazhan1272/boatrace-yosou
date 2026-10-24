"""ボートレース公式サイトの直前情報ページ（展示タイム・スタート展示・気象）の取得。

Open API の直前情報は約3時間おきにしか更新されず締切に間に合わないため、
締切が近いレースだけ公式サイトから直接取る。出力は Open API の previews と同じ形式。
アクセス間隔などは odds.py と共通（同じセッション・同じ待ち時間）。
"""
import re
from datetime import date

from .odds import _get

BEFOREINFO_URL = "https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={rno}&jcd={jcd:02d}&hd={hd}"

_TBODY_RE = re.compile(r'<tbody class="is-fs12[^"]*">(.*?)</tbody>', re.S)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_START_RE = re.compile(
    r'table1_boatImage1Number[^>]*>(\d)</span>.*?table1_boatImage1Time[^>]*>([^<]*)</span>', re.S)


def _text(html: str) -> str:
    return _TAG_RE.sub("", html).replace("&nbsp;", "").strip()


def _num(s: str):
    m = re.search(r"-?\d+(?:\.\d+)?", s or "")
    return float(m.group()) if m else None


def _start_timing(s: str):
    """'.04' → 0.04、'F.03' → -0.03（フライング）、'L' 等は None。"""
    m = re.fullmatch(r"(F?)\s*(\d*)\.(\d+)", s.strip())
    if not m:
        return None
    v = float(f"{m.group(2) or 0}.{m.group(3)}")
    return -v if m.group(1) else v


def parse_beforeinfo(html: str) -> dict | None:
    boats = {}
    for block in _TBODY_RE.findall(html):
        tds = [_text(t) for t in _TD_RE.findall(block)]
        if len(tds) < 13 or not tds[0].isdigit():
            continue
        ex_time = _num(tds[4])
        boats[int(tds[0])] = {
            "racer_boat_number": int(tds[0]),
            "racer_weight": _num(tds[3]),
            "racer_exhibition_time": ex_time if ex_time and ex_time > 0 else None,
            "racer_tilt_adjustment": _num(tds[5]),
            "racer_weight_adjustment": _num(tds[12]),
            "racer_course_number": None,
            "racer_start_timing": None,
        }
    if not boats or all(b["racer_exhibition_time"] is None for b in boats.values()):
        return None  # 展示前

    start = html[html.find("スタート展示"):]
    for course, (boat, st) in enumerate(_START_RE.findall(start)[:6], 1):
        if int(boat) in boats:
            boats[int(boat)]["racer_course_number"] = course
            boats[int(boat)]["racer_start_timing"] = _start_timing(st)

    def weather(cls_prefix, label=None):
        m = re.search(rf'is-{cls_prefix}(\d+)"', html)
        return int(m.group(1)) if m else None

    def label_value(title):
        m = re.search(rf"{title}</span>\s*<span[^>]*>([^<]*)</span>", html)
        return _num(m.group(1)) if m else None

    return {
        "race_wind": label_value("風速"),
        "race_wind_direction_number": weather("wind"),
        "race_wave": label_value("波高"),
        "race_weather_number": weather("weather"),
        "race_temperature": label_value("気温"),
        "race_water_temperature": label_value("水温"),
        "boats": [boats[k] for k in sorted(boats)],
    }


def fetch_beforeinfo(d: date, stadium: int, race_number: int) -> dict | None:
    """Open API の previews 1レース分と同じ形の dict。展示前なら None。"""
    html = _get(BEFOREINFO_URL.format(rno=race_number, jcd=stadium, hd=f"{d:%Y%m%d}"))
    data = parse_beforeinfo(html)
    if data:
        data.update({"race_date": d.isoformat(), "race_stadium_number": stadium, "race_number": race_number})
    return data
