"""APIレスポンスから 1行=1艇 の学習・予測用データフレームを作る。"""
import numpy as np
import pandas as pd


def race_id(r: dict) -> str:
    return f"{r['race_date'].replace('-', '')}_{r['race_stadium_number']:02d}_{r['race_number']:02d}"


PROGRAM_COLS = {
    "racer_boat_number": "boat",
    "racer_number": "racer_number",
    "racer_name": "racer_name",
    "racer_class_number": "cls",
    "racer_age": "age",
    "racer_weight": "weight",
    "racer_flying_count": "f_count",
    "racer_late_count": "l_count",
    "racer_average_start_timing": "avg_st",
    "racer_national_top_1_percent": "nat_win",
    "racer_national_top_2_percent": "nat_top2",
    "racer_national_top_3_percent": "nat_top3",
    "racer_local_top_1_percent": "loc_win",
    "racer_local_top_2_percent": "loc_top2",
    "racer_local_top_3_percent": "loc_top3",
    "racer_assigned_motor_top_2_percent": "motor_top2",
    "racer_assigned_motor_top_3_percent": "motor_top3",
    "racer_assigned_boat_top_2_percent": "boat_top2",
    "racer_assigned_boat_top_3_percent": "boat_top3",
}

PREVIEW_COLS = {
    "racer_course_number": "course",
    "racer_start_timing": "ex_st",
    "racer_weight_adjustment": "weight_adj",
    "racer_exhibition_time": "ex_time",
    "racer_tilt_adjustment": "tilt",
}
PREVIEW_RACE_COLS = {
    "race_wind": "wind",
    "race_wind_direction_number": "wind_dir",
    "race_wave": "wave",
    "race_weather_number": "weather",
    "race_temperature": "temp",
    "race_water_temperature": "water_temp",
}


def _program_rows(programs):
    for r in programs:
        base = {
            "race_id": race_id(r),
            "date": r["race_date"],
            "stadium": r["race_stadium_number"],
            "race_number": r["race_number"],
            "grade": r.get("race_grade_number"),
            "distance": r.get("race_distance"),
            "closed_at": r.get("race_closed_at"),
            "title": r.get("race_title"),
            "subtitle": r.get("race_subtitle"),
        }
        for b in r["boats"]:
            row = dict(base)
            row.update({v: b.get(k) for k, v in PROGRAM_COLS.items()})
            yield row


def _preview_rows(previews):
    for r in previews:
        race = {"race_id": race_id(r)}
        race.update({v: r.get(k) for k, v in PREVIEW_RACE_COLS.items()})
        for b in r["boats"]:
            row = dict(race)
            row["boat"] = b["racer_boat_number"]
            row.update({v: b.get(k) for k, v in PREVIEW_COLS.items()})
            yield row


def _result_rows(results):
    for r in results:
        rid = race_id(r)
        for b in r["boats"]:
            yield {
                "race_id": rid,
                "boat": b["racer_boat_number"],
                "place": b.get("racer_place_number"),
                "result_st": b.get("racer_start_timing"),
                "result_course": b.get("racer_course_number"),
            }


def build_frame(programs, previews=(), results=()) -> pd.DataFrame:
    df = pd.DataFrame(_program_rows(programs))
    if df.empty:
        return df
    pv = pd.DataFrame(_preview_rows(previews))
    if not pv.empty:
        # 展示タイム 0 は未計測扱い
        pv.loc[pv["ex_time"] <= 0, "ex_time"] = np.nan
        df = df.merge(pv, on=["race_id", "boat"], how="left")
    rs = pd.DataFrame(_result_rows(results))
    if not rs.empty:
        df = df.merge(rs, on=["race_id", "boat"], how="left")
    for c in list(PREVIEW_COLS.values()) + list(PREVIEW_RACE_COLS.values()) + ["place", "result_st"]:
        if c not in df:
            df[c] = np.nan
    df["date"] = pd.to_datetime(df["date"])
    return df




# ---------------------------------------------------------------- 過去成績

HISTORY_DAYS = 365   # 場×コース・選手×コース集計の期間
FORM_WINDOW = "90D"  # 選手の直近成績
MEET_WINDOW = "6D"   # 今節（同じ場で前日までの6日間）
COURSE_WINDOW = "365D"

# race_technique_number → 決まり手
TECHNIQUES = {1: "nige", 2: "sashi", 3: "makuri", 4: "makurizashi"}


def history_frame(results, previews=()) -> pd.DataFrame:
    """過去レースの 1行=1艇 の結果フレーム（集計の元データ）。

    racer_place_number の 7 以上は F・L・欠場・失格などのコード、
    負のスタートタイミングはフライング。
    """
    rows = []
    for r in results:
        rid = race_id(r)
        tech = r.get("race_technique_number")
        for b in r["boats"]:
            rows.append({
                "race_id": rid,
                "date": r["race_date"],
                "stadium": r["race_stadium_number"],
                "racer_number": b.get("racer_number"),
                "boat": b["racer_boat_number"],
                "course": b.get("racer_course_number"),
                "place": b.get("racer_place_number"),
                "st": b.get("racer_start_timing"),
                "technique": tech,
            })
    h = pd.DataFrame(rows)
    h["date"] = pd.to_datetime(h["date"])
    h = h.dropna(subset=["place", "course", "racer_number"])
    h[["course", "racer_number"]] = h[["course", "racer_number"]].astype(int)

    pv = pd.DataFrame(_preview_rows(previews))
    if not pv.empty:
        pv = pv[["race_id", "boat", "ex_time"]].copy()
        pv.loc[pv["ex_time"] <= 0, "ex_time"] = np.nan
        h = h.merge(pv, on=["race_id", "boat"], how="left")
    else:
        h["ex_time"] = np.nan

    finished = h["place"] <= 6
    h["one"] = 1.0
    h["win"] = (h["place"] == 1).astype(float)
    h["top2"] = (h["place"] <= 2).astype(float)
    h["top3"] = (h["place"] <= 3).astype(float)
    h["place_pts"] = h["place"].where(finished, 7).astype(float)  # 失格等は7着扱い
    valid_st = h["st"].notna() & (h["st"] >= 0)
    h["st_n"] = valid_st.astype(float)
    h["st_sum"] = h["st"].where(valid_st, 0.0)
    h["st_sq"] = h["st_sum"] ** 2
    h["flying"] = (h["st"] < 0).astype(float)
    for k, name in TECHNIQUES.items():
        h[f"win_{name}"] = h["win"] * (h["technique"] == k)
    # 展示タイムのレース内偏差（コースごとの癖を後で差し引く）
    h["ex_dev"] = h["ex_time"] - h.groupby("race_id")["ex_time"].transform("mean")
    h["ex_n"] = h["ex_dev"].notna().astype(float)
    h["ex_dev"] = h["ex_dev"].fillna(0.0)
    return h


def _rolling_sum(events: pd.DataFrame, keys: list[str], cols: list[str], window: str,
                 query: pd.DataFrame) -> pd.DataFrame:
    """keys ごとに、query の各日付の前日までの window 期間の合計を返す（当日は含まない）。"""
    daily = events.groupby(keys + ["date"])[cols].sum()
    q = query[keys + ["date"]].dropna().drop_duplicates()
    # 予測対象日の行をゼロで追加しておくと closed='left' の窓でそのまま引ける
    zeros = pd.DataFrame(0.0, index=pd.MultiIndex.from_frame(q), columns=cols)
    levels = list(range(len(keys) + 1))
    daily = pd.concat([daily, zeros]).groupby(level=levels).sum().sort_index()
    rolled = daily.reset_index(level=keys).groupby(keys)[cols].rolling(window, closed="left").sum()
    out = rolled.reset_index()
    return out.merge(q, on=keys + ["date"])


def _ratio(a, b, min_n=1):
    return a / b.where(b >= min_n)


def add_racer_features(df: pd.DataFrame, h: pd.DataFrame) -> pd.DataFrame:
    """コースに依存しない選手の成績: 直近90日、今節、予想ST。"""
    cols = ["one", "win", "top3", "place_pts", "st_n", "st_sum", "st_sq", "flying"]

    f = _rolling_sum(h, ["racer_number"], cols, FORM_WINDOW, df)
    st_mean = _ratio(f["st_sum"], f["st_n"])
    f = pd.DataFrame({
        "racer_number": f["racer_number"], "date": f["date"],
        "form_races": f["one"],
        "form_win": _ratio(f["win"], f["one"]),
        "form_top3": _ratio(f["top3"], f["one"]),
        "form_place": _ratio(f["place_pts"], f["one"]),
        "form_st": st_mean,
        "form_st_std": np.sqrt((_ratio(f["st_sq"], f["st_n"], 3) - st_mean ** 2).clip(lower=0)),
        "form_flying": f["flying"],
    })
    df = df.merge(f, on=["racer_number", "date"], how="left")

    m = _rolling_sum(h, ["racer_number", "stadium"], cols, MEET_WINDOW, df)
    m = pd.DataFrame({
        "racer_number": m["racer_number"], "stadium": m["stadium"], "date": m["date"],
        "meet_races": m["one"],
        "meet_win": _ratio(m["win"], m["one"]),
        "meet_top3": _ratio(m["top3"], m["one"]),
        "meet_place": _ratio(m["place_pts"], m["one"]),
        "meet_st": _ratio(m["st_sum"], m["st_n"]),
        "meet_st_n": m["st_n"],
    })
    df = df.merge(m, on=["racer_number", "stadium", "date"], how="left")

    # 予想ST: 今節(2走以上) → 直近90日 → 期別平均 の順に信頼する
    df["st_exp"] = (
        df["meet_st"].where(df["meet_st_n"] >= 2)
        .fillna(df["form_st"]).fillna(df["avg_st"])
    )
    return df


def add_course_features(df: pd.DataFrame, h: pd.DataFrame, key: str, sfx: str) -> pd.DataFrame:
    """コースに依存する特徴量。key はそのレースで想定するコースの列。

    base モデルは枠番(枠なり想定)、full モデルは展示の進入コースを使う。
    """
    df["_k"] = df[key].fillna(df["boat"]).astype(int)
    hk = h.rename(columns={"course": "_k"})

    # 場×コース: 入着率と決まり手、展示タイムの癖
    tech_cols = [f"win_{n}" for n in TECHNIQUES.values()]
    s = _rolling_sum(hk, ["stadium", "_k"], ["one", "win", "top2", "top3", "ex_n", "ex_dev"] + tech_cols,
                     COURSE_WINDOW, df)
    sc = pd.DataFrame({"stadium": s["stadium"], "_k": s["_k"], "date": s["date"],
                       f"sc_win{sfx}": _ratio(s["win"], s["one"], 30),
                       f"sc_top2{sfx}": _ratio(s["top2"], s["one"], 30),
                       f"sc_top3{sfx}": _ratio(s["top3"], s["one"], 30),
                       f"sc_ex_bias{sfx}": _ratio(s["ex_dev"], s["ex_n"], 30)})
    for c in tech_cols:
        sc[f"sc_{c}{sfx}"] = _ratio(s[c], s["one"], 30)
    df = df.merge(sc, on=["stadium", "_k", "date"], how="left")

    # 選手×コース
    r = _rolling_sum(hk, ["racer_number", "_k"], ["one", "win", "top3", "st_n", "st_sum"], COURSE_WINDOW, df)
    rc = pd.DataFrame({"racer_number": r["racer_number"], "_k": r["_k"], "date": r["date"],
                       f"rc_races{sfx}": r["one"],
                       f"rc_win{sfx}": _ratio(r["win"], r["one"], 3),
                       f"rc_top3{sfx}": _ratio(r["top3"], r["one"], 3),
                       f"rc_st{sfx}": _ratio(r["st_sum"], r["st_n"], 3)})
    df = df.merge(rc, on=["racer_number", "_k", "date"], how="left")

    # このコースでの予想STと、内側の艇とのST差（マイナス=内より早い→攻めやすい）
    df[f"st_exp{sfx}"] = df[f"rc_st{sfx}"].where(df[f"rc_races{sfx}"] >= 5).fillna(df["st_exp"])
    by_k = df.set_index(["race_id", "_k"])[f"st_exp{sfx}"]
    by_k = by_k[~by_k.index.duplicated()]
    inside = pd.MultiIndex.from_arrays([df["race_id"], df["_k"] - 1])
    df[f"st_gap_inside{sfx}"] = df[f"st_exp{sfx}"].values - by_k.reindex(inside).values

    # 展示タイム補正: レース内偏差から、その場・コースの平均的な偏差を差し引く
    ex_dev = df["ex_time"] - df.groupby("race_id")["ex_time"].transform("mean")
    df[f"ex_adj{sfx}"] = ex_dev - df[f"sc_ex_bias{sfx}"].fillna(0)

    # レース単位: この場の1コース1着率（イン有利度）
    in_rate = df.loc[df["_k"] == 1].groupby("race_id")[f"sc_win{sfx}"].first()
    df[f"stadium_in{sfx}"] = df["race_id"].map(in_rate)
    return df.drop(columns="_k")


# レース内での相対値を作る列と、その向き（True=小さいほど良い）
RELATIVE_COLS = {
    "nat_win": False, "nat_top2": False, "loc_win": False,
    "motor_top2": False, "boat_top2": False, "avg_st": True, "cls": True,
    "form_win": False, "form_top3": False, "form_st": True,
    "meet_place": True, "st_exp": True,
}
COURSE_RELATIVE_COLS = {"rc_win": False, "st_exp": True}
PREVIEW_RELATIVE_COLS = {"ex_time": True, "ex_st": True, "ex_adj_c": True}


def add_relative_features(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("race_id")
    rel = dict(RELATIVE_COLS)
    rel.update({f"{c}{s}": a for c, a in COURSE_RELATIVE_COLS.items() for s in ("_b", "_c")})
    rel.update(PREVIEW_RELATIVE_COLS)
    new = {}
    for col, asc in rel.items():
        if col not in df:
            continue
        new[f"{col}_rank"] = g[col].rank(ascending=asc, method="min")
        new[f"{col}_diff"] = df[col] - g[col].transform("mean")
    new["n_boats"] = g["boat"].transform("count")
    return pd.concat([df, pd.DataFrame(new, index=df.index)], axis=1)


def add_features(df: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    """build_frame の出力に全特徴量を付与する。history は予測日より前の結果を含むこと。"""
    df = add_racer_features(df, history)
    df["course_est"] = df["course"].fillna(df["boat"])
    df = add_course_features(df, history, "boat", "_b")
    df = add_course_features(df, history, "course_est", "_c")
    return add_relative_features(df)


def _course_feature_names(sfx):
    return [f"{c}{sfx}" for c in (
        "sc_win", "sc_top2", "sc_top3", "sc_win_nige", "sc_win_sashi", "sc_win_makuri",
        "sc_win_makurizashi", "rc_races", "rc_win", "rc_top3", "rc_st", "st_exp",
        "st_gap_inside", "stadium_in",
    )] + [f"{c}{sfx}_{s}" for c in COURSE_RELATIVE_COLS for s in ("rank", "diff")]


BASE_FEATURES = [
    "stadium", "race_number", "grade", "distance", "boat",
    "cls", "age", "weight", "f_count", "l_count", "avg_st",
    "nat_win", "nat_top2", "nat_top3", "loc_win", "loc_top2", "loc_top3",
    "motor_top2", "motor_top3", "boat_top2", "boat_top3",
    "form_races", "form_win", "form_top3", "form_place", "form_st", "form_st_std", "form_flying",
    "meet_races", "meet_win", "meet_top3", "meet_place", "meet_st",
    "st_exp", "n_boats",
] + [f"{c}_{s}" for c in RELATIVE_COLS for s in ("rank", "diff")] + _course_feature_names("_b")

PREVIEW_FEATURES = BASE_FEATURES + [
    "course", "ex_st", "weight_adj", "ex_time", "tilt",
    "wind", "wind_dir", "wave", "weather", "temp", "water_temp",
    "sc_ex_bias_c", "ex_adj_c",
] + [f"{c}_{s}" for c in PREVIEW_RELATIVE_COLS for s in ("rank", "diff")] + _course_feature_names("_c")

CATEGORICAL = ["stadium"]


def has_preview(df: pd.DataFrame) -> pd.Series:
    """レース単位で、全艇の展示タイムが揃っているか。"""
    return df.groupby("race_id")["ex_time"].transform(lambda s: s.notna().all())
