"""学習・検証・当日予測・サイト用JSON出力。"""
import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from . import model, odds, strategy
from .config import JST, MODEL_DIR, RACER_CLASSES, SITE_DATA_DIR, STADIUMS, today_jst
from .features import HISTORY_DAYS, add_features, build_frame, has_preview, history_frame
from .fetch import fetch_day, load_range

TRI_BETS = 5  # 推奨買い目: 3連単の上位N点（各100円）


# ---------------------------------------------------------------- 共通

def payouts_by_race(results) -> dict[str, dict]:
    from .features import race_id
    out = {}
    for r in results:
        p = r.get("payouts") or {}
        out[race_id(r)] = {
            kind: {x["combination"]: x["payout"] for x in (p.get(kind) or [])}
            for kind in ("trifecta", "exacta", "win")
        }
    return out


def make_dataset(start: date, end: date):
    """学習用: 期間内の全レースに特徴量と結果を付与したフレーム。"""
    raw = load_range(start - timedelta(days=HISTORY_DAYS), end)
    hist = history_frame(raw["results"], raw["previews"])
    in_range = [r for r in raw["programs"] if r["race_date"] >= start.isoformat()]
    df = build_frame(in_range, raw["previews"], raw["results"])
    df = add_features(df, hist)
    # 結果のある（中止でない）レースだけ使う
    has_winner = df.groupby("race_id")["place"].transform(lambda s: (s == 1).any())
    return df[has_winner].reset_index(drop=True), payouts_by_race(raw["results"])


# ---------------------------------------------------------------- 検証

def evaluate(df: pd.DataFrame, payouts: dict) -> dict:
    """df は列 p（正規化済み1着確率）を持つこと。"""
    n = hits = boat1 = win_ret = 0
    tri_hit = {1: 0, 5: 0, 10: 0}
    tri_ret = 0
    for rid, g in df.groupby("race_id"):
        winner = g.loc[g["place"] == 1, "boat"]
        if winner.empty:
            continue
        n += 1
        top = int(g.loc[g["p"].idxmax(), "boat"])
        w = int(winner.iloc[0])
        boat1 += w == 1
        pay = payouts.get(rid, {})
        if top == w:
            hits += 1
            win_ret += pay.get("win", {}).get(str(w), 0)
        tri = model.trifecta_probs(dict(zip(g["boat"].astype(int), g["p"])))
        actual = [c for c in pay.get("trifecta", {})]
        for k in tri_hit:
            if any(c in actual for c, _ in tri[:k]):
                tri_hit[k] += 1
        tri_ret += sum(pay.get("trifecta", {}).get(c, 0) for c, _ in tri[:TRI_BETS])
    return {
        "races": n,
        "logloss": round(model.log_loss_per_race(df), 4),
        "win_top1_accuracy": round(hits / n, 4),
        "baseline_boat1_accuracy": round(boat1 / n, 4),
        "win_top1_roi": round(win_ret / (100 * n), 4),
        "trifecta_hit_top1": round(tri_hit[1] / n, 4),
        "trifecta_hit_top5": round(tri_hit[5] / n, 4),
        "trifecta_hit_top10": round(tri_hit[10] / n, 4),
        f"trifecta_top{TRI_BETS}_roi": round(tri_ret / (100 * TRI_BETS * n), 4),
    }


def train_all(start: date, end: date, valid_days: int = 60):
    print(f"loading {start} .. {end}")
    df, payouts = make_dataset(start, end)
    split = pd.Timestamp(end - timedelta(days=valid_days - 1))
    tr, va = df[df["date"] < split], df[df["date"] >= split]
    print(f"rows: train={len(tr)} valid={len(va)}")

    report = {"train_period": [str(start), str(split.date() - timedelta(days=1))],
              "valid_period": [str(split.date()), str(end)], "models": {}}
    for kind in ("base", "full"):
        tr_k, va_k = tr, va
        if kind == "full":
            tr_k, va_k = tr[has_preview(tr)], va[has_preview(va)]
        print(f"[{kind}] training on {tr_k['race_id'].nunique()} races")
        booster = model.train(tr_k, kind, va_k)
        best = booster.best_iteration or booster.current_iteration()
        va_k = va_k.assign(p=model.win_probs(booster, va_k, kind))
        metrics = evaluate(va_k, payouts)
        metrics["best_iteration"] = best
        print(f"[{kind}] valid: {metrics}")
        report["models"][kind] = metrics

        # 検証で決めた反復数で全期間を学習し直す
        all_k = df if kind == "base" else df[has_preview(df)]
        final = model.train(all_k, kind, rounds=int(best * 1.1))
        importance = sorted(zip(final.feature_name(), final.feature_importance("gain")), key=lambda t: -t[1])
        model.save(final, kind, {
            "trained_at": datetime.now(JST).isoformat(timespec="seconds"),
            "period": [str(start), str(end)],
            "rounds": final.current_iteration(),
            "valid": metrics,
            "top_features": [[f, round(float(v), 1)] for f, v in importance[:15]],
        })

    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DATA_DIR / "backtest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def backtest_ev(start: date, end: date, train_start: date = date(2025, 4, 1)):
    """期待値買い目の検証。start より前のデータだけで学習したモデルで、
    確定オッズ（odds.fetch_final_range でキャッシュ済み）を使って回収率を出す。

    期間の前半で設定を選び、後半でその設定を評価する（同じ期間で選ぶと楽観的になるため）。
    """
    from itertools import product

    df, payouts = make_dataset(train_start, end)
    df = df[has_preview(df)]
    tr, te = df[df["date"] < pd.Timestamp(start)], df[df["date"] >= pd.Timestamp(start)]
    meta_path = MODEL_DIR / "full.json"
    best = json.loads(meta_path.read_text(encoding="utf-8"))["valid"]["best_iteration"] if meta_path.exists() else 380
    print(f"training full model on {tr['race_id'].nunique()} races (< {start})")
    booster = model.train(tr, "full", rounds=int(best * 1.1))
    te = te.assign(p=model.win_probs(booster, te, "full"))

    races = []
    for rid, g in te.groupby("race_id"):
        d = g["date"].iloc[0].date()
        book = odds.fetch_odds(d, int(g["stadium"].iloc[0]), int(g["race_number"].iloc[0]),
                               final=True, cached_only=True)
        if book["trifecta"] and rid in payouts:
            races.append((d, dict(zip(g["boat"].astype(int), g["p"])), book, payouts[rid]))
    print(f"races with odds: {len(races)}")
    # 前半/後半はレース数で半分に分ける（日付順）
    races.sort(key=lambda r: r[0])
    days = sorted({r[0] for r in races})
    mid_idx = len(races) // 2

    weights = (1.0, 0.7, 0.5, 0.3)
    cands = {w: [strategy.candidates(p, b, w) for _, p, b, _ in races] for w in weights}

    def run(cfg, part):
        bets = hits = ret = n_races = 0
        for i, ((_, _, _, pay), cs) in enumerate(zip(races, cands[cfg.model_weight])):
            if (part == "a" and i >= mid_idx) or (part == "b" and i < mid_idx):
                continue
            chosen = [c for c in cs if c["type"] in cfg.bet_types and c["ev"] >= cfg.ev_min
                      and c["prob"] >= cfg.prob_min][: cfg.max_picks]
            if not chosen:
                continue
            n_races += 1
            bets += len(chosen)
            r = strategy.settle(chosen, pay)
            hits += r > 0
            ret += r
        return {"races": n_races, "bets": bets, "hit_races": hits,
                "roi": round(ret / (100 * bets), 4) if bets else None}

    grid = [
        strategy.EVConfig(w, ev, pm, mp, bt)
        for w, ev, pm, mp, bt in product(
            weights, (1.0, 1.1, 1.2, 1.5, 2.0), (0.0, 0.01, 0.03), (1, 3, 5),
            (("trifecta", "exacta"), ("trifecta",), ("exacta",)),
        )
    ]
    rows = [(cfg, run(cfg, "a"), run(cfg, "b"), run(cfg, "all")) for cfg in grid]
    # 前半で一定数以上買った設定を前半ROI順に並べ、後半の成績を見る
    min_bets = max(30, mid_idx // 5)
    ranked = sorted((r for r in rows if r[1]["bets"] >= min_bets), key=lambda r: -r[1]["roi"])
    print(f"\nselect on first {mid_idx} races, test on last {len(races) - mid_idx} races")
    print("w    ev   pmin  max type          | A: bets  roi    | B: bets  roi    hit")
    for cfg, a, b, _ in ranked[:15]:
        print(f"{cfg.model_weight:<4} {cfg.ev_min:<4} {cfg.prob_min:<5} {cfg.max_picks:<3} {'+'.join(cfg.bet_types):<13} "
              f"| {a['bets']:>7} {a['roi']:<6} | {b['bets']:>7} {b['roi']}  {b['hit_races']}")
    default = run(strategy.DEFAULT, "all")
    print(f"\nDEFAULT {strategy.DEFAULT} -> {default}")

    report = {
        "period": [str(days[0]), str(days[-1])],
        "races": len(races),
        "note": "確定オッズ使用。実運用では締切前のオッズで判断するため結果は変動する",
        "default": {"config": strategy.DEFAULT.to_dict(), **default},
        "top_selected_on_first_half": [
            {"config": cfg.to_dict(), "first_half": a, "second_half": b} for cfg, a, b, _ in ranked[:10]
        ],
    }
    (SITE_DATA_DIR / "ev_backtest.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


# ---------------------------------------------------------------- 当日予測

def _race_prediction(g: pd.DataFrame, kind: str) -> dict:
    head = g.iloc[0]
    probs = dict(zip(g["boat"].astype(int), g["p"]))
    tri = model.trifecta_probs(probs)
    places = model.place_probs(tri)
    boats = []
    for _, b in g.sort_values("boat").iterrows():
        bn = int(b["boat"])
        boats.append({
            "boat": bn,
            "name": b["racer_name"],
            "class": RACER_CLASSES.get(b["cls"], ""),
            "nat_win": _num(b["nat_win"]),
            "motor_top2": _num(b["motor_top2"]),
            "ex_time": _num(b["ex_time"]),
            "ex_adj": _num(b["ex_adj_c"]),
            "course": _num(b["course"]),
            "meet_place": _num(b["meet_place"]),
            "meet_races": _num(b["meet_races"]),
            "st_exp": _num(b["st_exp_c"] if kind == "full" else b["st_exp_b"]),
            "win": round(float(b["p"]), 4),
            "top2": round(places[bn][0], 4),
            "top3": round(places[bn][1], 4),
        })
    return {
        "race_id": head["race_id"],
        "stadium": int(head["stadium"]),
        "stadium_name": STADIUMS.get(int(head["stadium"]), ""),
        "race_number": int(head["race_number"]),
        "closed_at": head["closed_at"],
        "title": head["title"],
        "subtitle": head["subtitle"],
        "model": kind,
        "stadium_in": _num(head["stadium_in_c"]),
        "boats": boats,
        "trifecta": [[c, round(q, 4)] for c, q in tri[:20]],
        "exacta": [[c, round(q, 4)] for c, q in model.exacta_probs(probs)[:10]],
    }


def _num(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)


def _score(race: dict, result: dict | None, pay: dict | None):
    """予測にレース結果と的中判定を付ける。"""
    if not result or not pay or not pay.get("trifecta"):
        return
    order = sorted((b for b in result["boats"] if b.get("racer_place_number")),
                   key=lambda b: b["racer_place_number"])
    combos = list(pay["trifecta"])
    picks = [c for c, _ in race["trifecta"][:TRI_BETS]]
    top = max(race["boats"], key=lambda b: b["win"])["boat"]
    rank = next((i + 1 for i, (c, _) in enumerate(race["trifecta"]) if c in combos), None)
    race["result"] = {
        "order": [b["racer_boat_number"] for b in order[:3]],
        "trifecta": combos,
        "trifecta_payout": sum(pay["trifecta"].values()),
        "win_payout": pay["win"].get(str(top), 0),
        "honmei_hit": bool(order) and order[0]["racer_boat_number"] == top,
        "trifecta_rank": rank,
        "bet": 100 * TRI_BETS,
        "return": sum(pay["trifecta"].get(c, 0) for c in picks),
    }
    ev_bets = race.get("ev", {}).get("picks", [])
    if ev_bets:
        race["result"]["ev_bet"] = 100 * len(ev_bets)
        race["result"]["ev_return"] = strategy.settle(ev_bets, pay)


ODDS_WINDOW_MIN = 25  # 締切この分数前からオッズを取得して期待値買い目を出す


def _attach_ev(race: dict, d: date, now: datetime):
    """締切が近いレースだけ公式サイトのオッズを取り、期待値買い目を付ける。"""
    if not race["closed_at"]:
        return
    closed = datetime.strptime(race["closed_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
    if not now < closed <= now + timedelta(minutes=ODDS_WINDOW_MIN):
        return
    try:
        book = odds.fetch_odds(d, race["stadium"], race["race_number"], final=False)
    except Exception as e:  # オッズが取れなくても予想自体は出す
        print(f"  odds failed {race['race_id']}: {e}")
        return
    if not book["trifecta"]:
        return
    probs = {b["boat"]: b["win"] for b in race["boats"]}
    race["ev"] = {
        "odds_at": book["fetched_at"],
        "config": strategy.DEFAULT.to_dict(),
        # 公式オッズの転載にならないよう、推奨した買い目のオッズだけ残す
        "picks": strategy.picks(probs, book),
    }


def predict_day(d: date) -> dict:
    from .features import race_id

    programs = fetch_day("programs", d, force=True)
    previews = fetch_day("previews", d, force=True)
    results = fetch_day("results", d, force=True)
    if not programs:
        return {"date": str(d), "races": []}

    past = load_range(d - timedelta(days=HISTORY_DAYS), d - timedelta(days=1))
    df = build_frame(programs, previews)
    df = add_features(df, history_frame(past["results"], past["previews"]))
    df["full"] = has_preview(df)

    boosters = {k: model.load(k) for k in ("base", "full")}
    df["p"] = model.win_probs(boosters["base"], df, "base")
    if df["full"].any():
        sub = df[df["full"]]
        df.loc[sub.index, "p"] = model.win_probs(boosters["full"], sub, "full")

    out_path = SITE_DATA_DIR / "predictions" / f"{d:%Y%m%d}.json"
    previous = {}
    if out_path.exists():
        previous = {r["race_id"]: r for r in json.loads(out_path.read_text(encoding="utf-8"))["races"]}

    now_dt = datetime.now(JST)
    now = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    res_by_id = {race_id(r): r for r in results}
    pays = payouts_by_race(results)
    races = []
    for rid, g in df.groupby("race_id", sort=False):
        race = _race_prediction(g, "full" if g["full"].iloc[0] else "base")
        old = previous.get(rid)
        if old and race["closed_at"] and race["closed_at"] <= now:
            # 締切後は予測を凍結し、後出しにならないようにする
            race = {k: v for k, v in old.items() if k != "result"}
        else:
            _attach_ev(race, d, now_dt)
            if "ev" not in race and old and "ev" in old:
                race["ev"] = old["ev"]  # 今回取れなければ前回の買い目を維持
        _score(race, res_by_id.get(rid), pays.get(rid))
        races.append(race)
    races.sort(key=lambda r: (r["stadium"], r["race_number"]))

    data = {"date": str(d), "updated_at": now, "races": races}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return data


def build_index():
    """日付一覧と日別成績をまとめた index.json を作る。"""
    days = []
    for path in sorted((SITE_DATA_DIR / "predictions").glob("*.json"), reverse=True):
        data = json.loads(path.read_text(encoding="utf-8"))
        scored = [r["result"] for r in data["races"] if "result" in r]
        days.append({
            "date": data["date"],
            "file": f"predictions/{path.name}",
            "races": len(data["races"]),
            "scored": len(scored),
            "honmei_hits": sum(s["honmei_hit"] for s in scored),
            "trifecta_hits": sum(1 for s in scored if s["trifecta_rank"] and s["trifecta_rank"] <= TRI_BETS),
            "bet": sum(s["bet"] for s in scored),
            "return": sum(s["return"] for s in scored),
            "ev_races": sum(1 for s in scored if s.get("ev_bet")),
            "ev_hits": sum(1 for s in scored if s.get("ev_return")),
            "ev_bet": sum(s.get("ev_bet", 0) for s in scored),
            "ev_return": sum(s.get("ev_return", 0) for s in scored),
        })
    meta = {}
    for kind in ("base", "full"):
        p = MODEL_DIR / f"{kind}.json"
        if p.exists():
            meta[kind] = json.loads(p.read_text(encoding="utf-8"))
    index = {"updated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
             "tri_bets": TRI_BETS, "days": days, "models": meta}
    (SITE_DATA_DIR / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")


def update(d: date | None = None):
    """定期実行用: 前日の結果確定 + 当日予測の更新 + index 再生成。"""
    d = d or today_jst()
    yesterday = d - timedelta(days=1)
    # 前日分は、締切前に予測を出していた場合だけ結果を付ける（後付け予測は作らない）
    days = [d]
    if (SITE_DATA_DIR / "predictions" / f"{yesterday:%Y%m%d}.json").exists():
        days.insert(0, yesterday)
    for day in days:
        data = predict_day(day)
        print(f"{day}: {len(data['races'])} races")
    build_index()
