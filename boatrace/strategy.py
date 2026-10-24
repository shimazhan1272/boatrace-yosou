"""オッズと予測確率から期待値ベースの推奨買い目を選ぶ。

期待値 EV = 出現確率 × オッズ（100円あたりの払戻期待 / 100）。出現確率はモデルの確率。
（model_weight を 1 未満にすると、大穴の過大評価を抑えるためにオッズから逆算した市場確率と混ぜる:
    p = w * p_model + (1 - w) * p_market ）
"""
from dataclasses import asdict, dataclass

from . import model


@dataclass(frozen=True)
class EVConfig:
    model_weight: float = 1.0   # w: モデル確率の重み（1.0 = 出現確率そのまま）
    ev_min: float = 1.2         # これ以上の期待値の買い目だけ買う
    prob_min: float = 0.01      # 当たる確率がこれ未満の大穴は買わない
    max_picks: int = 3          # 1レースあたりの最大点数
    bet_types: tuple = ("trifecta", "exacta")

    def to_dict(self):
        return asdict(self)


DEFAULT = EVConfig()


def _market_probs(odds: dict[str, float]) -> dict[str, float]:
    inv = {c: 1 / o for c, o in odds.items() if o > 0}
    total = sum(inv.values())
    return {c: v / total for c, v in inv.items()}


def candidates(win_probs: dict[int, float], odds: dict, weight: float) -> list[dict]:
    """全買い目の期待値を計算する（券種をまたいで EV 降順）。"""
    model_probs = {
        "trifecta": dict(model.trifecta_probs(win_probs)),
        "exacta": dict(model.exacta_probs(win_probs)),
    }
    out = []
    for kind, probs in model_probs.items():
        book = odds.get(kind) or {}
        market = _market_probs(book)
        for combo, o in book.items():
            pm = probs.get(combo, 0.0)
            p = weight * pm + (1 - weight) * market[combo]
            out.append({"type": kind, "combo": combo, "odds": o, "prob": p, "model_prob": pm, "ev": p * o})
    out.sort(key=lambda c: -c["ev"])
    return out


def picks(win_probs: dict[int, float], odds: dict, cfg: EVConfig = DEFAULT) -> list[dict]:
    chosen = [
        c for c in candidates(win_probs, odds, cfg.model_weight)
        if c["type"] in cfg.bet_types and c["ev"] >= cfg.ev_min and c["prob"] >= cfg.prob_min
    ]
    return [
        {k: (round(v, 4) if isinstance(v, float) else v) for k, v in c.items()}
        for c in chosen[: cfg.max_picks]
    ]


def attach_odds(race: dict, book: dict, odds_at: str):
    """予想 dict に、表示中の3連単上位のオッズを付ける（期待値は画面で 確率 × オッズ を計算）。"""
    tri = book.get("trifecta") or {}
    race["odds"] = {
        "at": odds_at,
        "trifecta": {c: tri[c] for c, _ in race["trifecta"] if c in tri},
    }


def attach(race: dict, book: dict, odds_at: str, cfg: EVConfig = DEFAULT):
    """オッズに加えて、期待値推奨の買い目を付ける（締切前のオッズで使う）。"""
    attach_odds(race, book, odds_at)
    probs = {b["boat"]: b["win"] for b in race["boats"]}
    race["ev"] = {"odds_at": odds_at, "config": cfg.to_dict(), "picks": picks(probs, book, cfg)}


def settle(bets: list[dict], payouts: dict) -> int:
    """払戻額の合計（各100円購入）。payouts は pipeline.payouts_by_race の1レース分。"""
    return sum(payouts.get(b["type"], {}).get(b["combo"], 0) for b in bets)
