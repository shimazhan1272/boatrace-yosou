const $ = (s, el = document) => el.querySelector(s);
const pct = (v) => (v == null ? "-" : (v * 100).toFixed(1) + "%");
const yen = (v) => v.toLocaleString("ja-JP") + "円";
// "1-2-3" → 艇番カラーの丸数字を「-」でつないだ表示
const combo = (c) => `<span class="combo">${c.split("-").map((n) => `<span class="ball l${n}">${n}</span>`).join('<span class="sep">-</span>')}</span>`;
const ODDS_WINDOW = 25;
// 直前情報・オッズをその場で取得する API（Render）。当日のレースは自動で、更新ボタンでも呼ぶ
const API_BASE = new URLSearchParams(location.search).get("api") || "https://boatrace-yosou-api.onrender.com";
const todayJST = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);

let index, day;
const state = { date: null, stadium: null, race: null };

// GitHub Pages は10分キャッシュされるので、クエリを付けて常に最新を取る
async function getJSON(path, version = Math.floor(Date.now() / 60000)) {
  const res = await fetch(`${path}?t=${encodeURIComponent(version)}`, { cache: "no-store" });
  if (!res.ok) throw new Error(path + " " + res.status);
  return res.json();
}

async function init() {
  index = await getJSON("data/index.json");
  const input = $("#date");
  const dates = index.days.map((d) => d.date).sort();
  if (dates.length) { input.min = dates[0]; input.max = dates[dates.length - 1]; }
  input.addEventListener("change", () => input.value && select({ date: input.value, stadium: state.stadium, race: null }));
  $("#prev-day").onclick = () => stepDay(-1);
  $("#next-day").onclick = () => stepDay(1);
  $("#today").onclick = () => select({ date: todayJST(), stadium: state.stadium, race: null });
  $("#refresh").onclick = refresh;
  window.addEventListener("hashchange", () => select(parseHash()));
  // タブに戻ってきたら、待たずに最新を取りに行く
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    mergeLiveDay();
    if (needsLive(currentRace())) fetchLive(true);
  });
  const m = index.models.full || index.models.base;
  if (m) {
    $("#model-info").textContent =
      `モデル学習: ${m.trained_at.slice(0, 10)}（期間 ${m.period[0]}〜${m.period[1]}）`;
  }
  // 無料枠の API は停止していることがあるので、ページを開いた時点で起こしておく
  if (API_BASE) fetch(`${API_BASE}/health`).catch(() => {});
  if (index.days.length) await select(parseHash());
  else $("#empty").hidden = false;
}

const LIVE_INTERVAL = 60 * 1000;  // 自動取得の間隔（API 側でオッズは60秒キャッシュ）
let liveTimer = null;
let liveBusy = false;

const nowJST = () => new Date(Date.now() + 9 * 3600e3).toISOString().replace("T", " ").slice(0, 19);
const currentRace = () => day?.races.find((r) => r.stadium === state.stadium && r.race_number === state.race);

// 当日のレースで、まだ締切前か、締切後でも締切時オッズが無いものは API から自動で取り直す
function needsLive(r) {
  if (!API_BASE || !r || state.date !== todayJST()) return false;
  return r.closed_at > nowJST() || r.odds?.at !== "締切時";
}

// API で直前情報・オッズを取り直して予想し直す。silent は自動取得（ボタン表示を変えない）
async function fetchLive(silent) {
  if (liveBusy) return;
  liveBusy = true;
  const msg = $("#update-msg");
  const key = { stadium: state.stadium, race: state.race };
  const slow = setTimeout(() => (msg.textContent = "サーバー起動中（最大1分ほどかかります）…"), 4000);
  try {
    if (!silent) msg.textContent = "直前情報・オッズを取得中…";
    // 更新ボタンは fresh=1 でキャッシュを使わず取り直す（同じレースは15秒に1回まで）
    const res = await fetch(`${API_BASE}/api/race?date=${state.date}&stadium=${key.stadium}&race=${key.race}${silent ? "" : "&fresh=1"}`);
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || res.status);
    const i = day.races.findIndex((r) => r.stadium === key.stadium && r.race_number === key.race);
    if (i >= 0) {
      body.result = day.races[i].result;  // 結果・今節成績は公開データ側のものを使う
      body.meet_day = day.races[i].meet_day;
      body.boats.forEach((b, k) => (b.meet = day.races[i].boats[k]?.meet));
    }
    if (i >= 0) day.races[i] = body; else day.races.push(body);
    // 取得中に別のレースへ移っていたら、画面はそのまま（データだけ更新）
    if (state.stadium === key.stadium && state.race === key.race) await select({ ...state }, false);
    const what = body.live.exhibition ? "直前情報" : "出走表のみ（展示前）";
    const odds = body.odds ? (body.odds.at === "締切時" ? "締切時オッズ" : "オッズ " + body.odds.at.slice(11, 16)) : "オッズ未発表";
    msg.textContent = `${silent ? "自動取得" : "取得"}: ${what}・${odds}`;
  } catch (e) {
    msg.textContent = (silent ? "自動取得に失敗: " : "更新に失敗: ") + e.message;
  } finally {
    clearTimeout(slow);
    liveBusy = false;
  }
}

// 当日の全レース: サーバーが毎分集めた直前情報で予想し直した結果を取り込む。
// 定期実行（GitHub）を待たずに、開いていないレースも展示後すぐ「直前情報あり」になる
let dayBusy = false;
async function mergeLiveDay() {
  if (!API_BASE || dayBusy || !day || day.date !== todayJST()) return;
  dayBusy = true;
  try {
    const res = await fetch(`${API_BASE}/api/day?date=${day.date}`);
    if (!res.ok) return;
    const live = await res.json();
    const now = nowJST();
    let changed = false;
    for (const lr of live.races) {
      const i = day.races.findIndex((r) => r.race_id === lr.race_id);
      if (i < 0) continue;
      const sr = day.races[i];
      if (sr.live) continue;  // レース単位で取り直したものの方が新しい
      // 締切前は常に最新、締切後は記録が出走表のみで、締切前に直前情報が取れていた場合だけ差し替える
      if (!(sr.closed_at > now || (sr.model === "base" && lr.model === "full"))) continue;
      if (JSON.stringify(sr.boats.map((b) => b.win)) === JSON.stringify(lr.boats.map((b) => b.win)) && sr.model === lr.model) continue;
      for (const k of ["result", "meet_day", "odds", "ev", "final_odds_checked"]) if (sr[k] !== undefined) lr[k] = sr[k];
      lr.boats.forEach((b, k) => (b.meet = sr.boats[k]?.meet));
      day.races[i] = lr;
      changed = true;
    }
    if (changed) await select({ ...state }, false);
  } catch (e) {
    // 失敗しても公開データの表示のまま
  } finally {
    dayBusy = false;
  }
}

// 表示中のレースの自動取得と、当日全レースの取り込みを（再）開始する
function scheduleLive() {
  clearInterval(liveTimer);
  const today = day && day.date === todayJST();
  if (!today) return;
  mergeLiveDay();
  if (needsLive(currentRace())) fetchLive(true);
  liveTimer = setInterval(() => {
    if (document.visibilityState !== "visible") return;
    mergeLiveDay();
    if (needsLive(currentRace())) fetchLive(true);
  }, LIVE_INTERVAL);
}

// 更新ボタン: 当日のレースは API で取り直し、それ以外は公開データを読み直す
async function refresh() {
  const btn = $("#refresh");
  const msg = $("#update-msg");
  btn.disabled = true;
  btn.textContent = "更新中…";
  try {
    if (API_BASE && state.date === todayJST() && state.race) return await fetchLive(false);
    const before = day?.updated_at;
    index = await getJSON("data/index.json", Date.now());
    day = null;
    await select({ ...state });
    msg.textContent = day && day.updated_at !== before ? "最新データを反映しました" : "新しいデータはまだありません";
  } catch (e) {
    msg.textContent = "更新に失敗しました: " + e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "↻ 更新";
  }
}

// データのある前後の日へ移動する
function stepDay(dir) {
  const dates = index.days.map((d) => d.date).sort();
  const i = dates.indexOf(state.date);
  const next = dates[i + dir];
  if (next) select({ date: next, stadium: state.stadium, race: null });
}

// URL の #日付/場/R で選択状態を共有・ブックマークできるようにする
function parseHash() {
  const [date, stadium, race] = location.hash.slice(1).split("/");
  return { date, stadium: Number(stadium) || null, race: Number(race) || null };
}

async function select({ date, stadium, race }, live = true) {
  const entry = date ? index.days.find((d) => d.date === date) : index.days[0];
  $("#date").value = date || index.days[0].date;
  if (!entry) {
    // カレンダーでデータの無い日を選んだ場合
    state.date = date;
    const dates = index.days.map((d) => d.date).sort();
    $("#stadiums").innerHTML = $("#race-tabs").innerHTML = $("#races").innerHTML = "";
    $("#day-note").hidden = true;
    $("#empty").hidden = false;
    $("#empty").textContent = `${date} の予想はありません（表示できる期間: ${dates[0]} 〜 ${dates[dates.length - 1]}）。`;
    updateDayButtons();
    clearInterval(liveTimer);
    return;
  }
  if (!day || day.date !== entry.date) day = await getJSON("data/" + entry.file, index.updated_at);
  state.date = entry.date;
  $("#empty").textContent = "この日の予想はありません。";
  const note = $("#day-note");
  note.hidden = !day.backtest;
  note.innerHTML = day.backtest ? `<b>検証データ</b> ${day.note ?? ""}` : "";
  updateDayButtons();
  $("#status").hidden = false;

  const stadiums = [...new Map(day.races.map((r) => [r.stadium, r.stadium_name])).entries()];
  state.stadium = stadiums.some(([n]) => n === stadium) ? stadium : stadiums[0]?.[0];
  const races = day.races.filter((r) => r.stadium === state.stadium);
  state.race = races.some((r) => r.race_number === race) ? race : defaultRace(races);

  const hash = `#${state.date}/${state.stadium}/${state.race}`;
  if (location.hash !== hash) history.replaceState(null, "", hash);
  renderTabs(stadiums, races);
  renderRace(races.find((r) => r.race_number === state.race));
  updateTime();
  if (live) scheduleLive();
}

// 最終更新: 表示中のレースを API で取り直していればその時刻、なければ公開データの更新時刻
function updateTime() {
  const r = currentRace();
  const t = r?.live?.fetched_at ?? (day.backtest ? null : day.updated_at);
  $("#updated").textContent = t ? `最終更新 ${t.slice(11, r?.live ? 19 : 16)}` : "";
}

// 未指定なら次に締切を迎えるレース、全て終わっていれば1R
function defaultRace(races) {
  const now = new Date(Date.now() + 9 * 3600e3).toISOString().replace("T", " ").slice(0, 19); // JST
  return (races.find((r) => r.closed_at > now) ?? races[0])?.race_number;
}

function renderTabs(stadiums, races) {
  const nav = $("#stadiums");
  nav.innerHTML = "";
  for (const [num, name] of stadiums) {
    nav.append(tab(name, num === state.stadium, () => select({ ...state, stadium: num, race: null })));
  }
  const rnav = $("#race-tabs");
  rnav.innerHTML = "";
  for (const r of races) {
    const label = `<b>${r.race_number}R</b><small>${r.closed_at?.slice(11, 16) ?? ""}</small>`;
    const b = tab(label, r.race_number === state.race, () => select({ ...state, race: r.race_number }));
    if (r.result) b.classList.add("done");
    rnav.append(b);
  }
}

function updateDayButtons() {
  const dates = index.days.map((d) => d.date).sort();
  $("#today").disabled = state.date === todayJST();
  $("#prev-day").disabled = !dates.some((d) => d < state.date);
  $("#next-day").disabled = !dates.some((d) => d > state.date);
}

function tab(html, pressed, onclick) {
  const b = document.createElement("button");
  b.innerHTML = html;
  b.setAttribute("aria-pressed", pressed);
  b.onclick = onclick;
  return b;
}

// 3連単の選択（レースごと。自動更新で再描画しても残す）
const selections = new Map();
const picked = (r) => {
  if (!selections.has(r.race_id)) selections.set(r.race_id, new Set());
  return selections.get(r.race_id);
};

// 選択した買い目の合成オッズ = 1 / Σ(1/オッズ)。どれが当たっても払戻が同じになるよう賭け金を配分した場合のオッズ
function renderPicked(box, r) {
  const out = $(".tri-sel", box);
  const set = picked(r);
  if (!set.size) {
    out.innerHTML = `<span class="muted">組番をクリックすると選択でき、合成オッズを計算します</span>`;
    return;
  }
  const odds = r.odds?.trifecta ?? {};
  const prob = Object.fromEntries(r.trifecta);
  const sel = [...set];
  const p = sel.reduce((a, c) => a + (prob[c] ?? 0), 0);
  const missing = sel.filter((c) => !odds[c]);
  const synth = missing.length ? null : 1 / sel.reduce((a, c) => a + 1 / odds[c], 0);
  const ev = synth ? p * synth : null;
  out.innerHTML = `
    <span>選択 <b>${sel.length}</b>点</span>
    <span>合成オッズ <b>${synth ? synth.toFixed(2) + "倍" : "-"}</b></span>
    <span>的中確率 <b>${pct(p)}</b></span>
    <span>期待値 <b class="${ev >= 1 ? "plus" : ""}">${ev ? Math.round(ev * 100) + "%" : "-"}</b></span>
    ${missing.length ? `<small class="muted">オッズ未取得の組番があります</small>` : ""}
    <button type="button" class="sel-clear">選択解除</button>`;
  $(".sel-clear", out).onclick = () => {
    set.clear();
    box.querySelectorAll("li.sel").forEach((li) => {
      li.classList.remove("sel");
      li.querySelector(".rank").textContent = r.trifecta.findIndex(([c]) => c === li.dataset.c) + 1;
    });
    renderPicked(box, r);
  };
}

// 今節成績: 日ごとに レース番号（枠番の色）・進入・ST・着順。表示中のレースより前の走だけ
const PLACE_LABEL = { 14: "F", 15: "L", 16: "欠" };
const placeLabel = (p) => (p <= 6 ? p : PLACE_LABEL[p] ?? "失");
function meetCell(b, r) {
  const runs = (b.meet ?? []).filter(([day, rn]) => day < r.meet_day || (day === r.meet_day && rn < r.race_number));
  if (!runs.length) return '<span class="muted">-</span>';
  const finished = runs.filter((x) => x[5] <= 6);
  const avg = finished.length ? (finished.reduce((a, x) => a + x[5], 0) / finished.length).toFixed(2) : "-";
  const days = [...new Set(runs.map((x) => x[0]))];
  const label = `<div class="md md-label"><div class="md-h">平均${avg}</div><div class="mr"><span>R</span><span>進入</span><span>ST</span><span>着</span></div></div>`;
  return `<div class="meet-grid">${label}${days.map((day) => `
    <div class="md"><div class="md-h">${day === 1 ? "初日" : day + "日目"}</div><div class="md-races">${
      runs.filter((x) => x[0] === day).map(([, rn, boat, course, st, place]) => `
        <div class="mr"><span class="mr-r l${boat}">${rn}</span><span>${course ?? "-"}</span><span>${fmtST(st)}</span>`
        + `<span class="mr-p ${place === 1 ? "first" : place > 6 ? "bad" : ""}">${placeLabel(place)}</span></div>`).join("")
    }</div></div>`).join("")}</div>`;
}

// ".02" / "F.03" 形式の ST 表示
const fmtST = (st) => (st == null ? "-" : (st < 0 ? "F" : "") + "." + String(Math.round(Math.abs(st) * 100)).padStart(2, "0"));

// スタート展示の艇（右向き。後ろにモーター、上に選手）を艇番の色で描く
const LANE_COLORS = { 1: ["#ffffff", "#8a8a8a"], 2: ["#2b2b2b", "#111"], 3: ["#e5322d", "#a61e1a"],
                      4: ["#1f5fbf", "#123f85"], 5: ["#f2c500", "#b08f00"], 6: ["#1f9a4b", "#126b32"] };
function boatSVG(n, right) {
  const [fill, line] = LANE_COLORS[n] ?? ["#999", "#666"];
  return `<svg class="st-boat" style="right:${right}%" viewBox="0 0 56 26" aria-hidden="true">
    <rect x="1" y="9" width="7" height="9" rx="1.5" fill="#9aa3ad" stroke="#5f6770"/>
    <rect x="3" y="6" width="9" height="3" rx="1" fill="#5f6770"/>
    <path d="M9 15 L44 15 Q54 16.5 55 18.5 Q50 22 42 22 L11 22 Q9 22 9 20 Z" fill="#f4f6f8" stroke="${line}" stroke-width="1.4"/>
    <path d="M11 18.5 L50 18.5" stroke="${line}" stroke-width="1.6"/>
    <path d="M20 15 Q21 9 27 8.5 L33 8.5 Q36 9 36 15 Z" fill="${fill}" stroke="${line}" stroke-width="1.2"/>
    <circle cx="31" cy="6.5" r="4.2" fill="${fill}" stroke="${line}" stroke-width="1.2"/>
    <path d="M32 5 L35.5 5.5" stroke="${line === "#8a8a8a" ? "#555" : "#fff"}" stroke-width="1.2" stroke-linecap="round"/>
  </svg>`;
}

// スタート展示: 上から1〜6コースの順に、入った艇・ST の早さ（位置）・ST
function renderStart(box, r) {
  const rows = $(".st-rows", box);
  const entered = r.boats.filter((b) => b.course != null).sort((a, b) => a.course - b.course);
  if (!entered.length) {
    rows.innerHTML = `<p class="muted st-none">展示前（展示後に自動で表示されます）</p>`;
    return;
  }
  rows.innerHTML = entered.map((b) => {
    const st = b.ex_st;
    // ST 0.00 でスタートライン（右端）、0.50 で左端寄り
    const back = st == null ? 50 : Math.min(Math.max(st, 0), 0.5) / 0.5 * 78;
    const moved = b.course !== b.boat;
    return `<div class="st-row">
      <span class="lane l${b.boat} ${moved ? "moved-lane" : ""}" title="${moved ? `${b.boat}号艇が${b.course}コースから進入` : `${b.course}コース`}">${b.boat}</span>
      <div class="st-track">${boatSVG(b.boat, back)}</div>
      <span class="st-time ${st != null && st < 0 ? "flying" : ""}">${fmtST(st)}</span>
    </div>`;
  }).join("");
}

function renderRace(r) {
  const box = $("#races");
  box.innerHTML = "";
  $("#empty").hidden = Boolean(r);
  if (!r) return;
  const tpl = $("#race-tpl");
  {
    const el = tpl.content.cloneNode(true);
    $(".race-no", el).textContent = `${r.stadium_name} ${r.race_number}R`;
    $(".race-title", el).textContent = [r.subtitle, r.title].filter(Boolean).join(" / ");
    const badge = $(".model", el);
    badge.textContent = r.model === "full" ? "直前情報あり" : "出走表のみ";
    badge.classList.add(r.model);
    if (r.recorded_after_close) badge.title = "直前情報は締切前に取得。予想の記録は締切後（定期実行の遅れのため）";
    const moved = r.boats.some((b) => b.course != null && b.course !== b.boat);
    if (moved) badge.insertAdjacentHTML("afterend", ' <span class="badge moved-badge">進入変化</span>');
    $(".closed", el).textContent = [
      r.stadium_in != null ? `イン1着率 ${pct(r.stadium_in)}` : "",
      r.closed_at ? "締切 " + r.closed_at.slice(11, 16) : "",
    ].filter(Boolean).join("　");

    $("tbody", el).innerHTML = r.boats.map((b) => `
      <tr>
        <td><span class="lane l${b.boat}">${b.boat}</span></td>
        <td class="name">${b.name}<span class="cls">${b.class}</span></td>
        <td class="num hide-sm">${b.nat_win?.toFixed(2) ?? "-"}</td>
        <td class="num hide-sm">${b.st_exp != null ? "." + Math.round(b.st_exp * 100).toString().padStart(2, "0") : "-"}</td>
        <td class="num hide-sm">${b.ex_time != null ? `${b.ex_time.toFixed(2)}<small class="${b.ex_adj < 0 ? "good" : "muted"}">(${b.ex_adj > 0 ? "+" : ""}${b.ex_adj.toFixed(2)})</small>` : "-"}</td>
        <td class="num win">${pct(b.win)}</td>
        <td class="num">${pct(b.top2)}</td>
        <td class="num">${pct(b.top3)}</td>
        <td class="meet">${meetCell(b, r)}</td>
      </tr>`).join("");

    renderStart($(".start-ex", el), r);

    const res = r.result;
    const hitCombos = new Set(res?.trifecta ?? []);
    // 3連単上位20点: 出現確率・オッズ・期待値（= 出現確率 × オッズ、%表示。100%超は払戻の期待が掛け金を上回る）
    const odds = r.odds?.trifecta ?? {};
    const head = `<li class="tri-head"><span class="rank">順</span><span>組番</span><span class="p">確率</span><span class="o">オッズ</span><span class="ev">期待値</span></li>`;
    const rows = r.trifecta.slice(0, 20).map(([c, p], i) => {
      const o = odds[c];
      const ev = o ? p * o : null;
      const sel = picked(r).has(c);
      return `<li data-c="${c}" class="pick ${hitCombos.has(c) ? "hit" : ""} ${sel ? "sel" : ""}" title="クリックで選択"><span class="rank">${sel ? "✓" : i + 1}</span>${combo(c)}`
        + `<span class="p">${pct(p)}</span><span class="o">${o ? o.toFixed(1) : "-"}</span>`
        + `<span class="ev ${ev >= 1 ? "plus" : ""}">${ev ? Math.round(ev * 100) + "%" : "-"}</span></li>`;
    });
    // PC は 1〜10位 / 11〜20位 の2列（各列に見出し）、スマホは1列
    $(".tri", el).innerHTML = head + rows.slice(0, 10).join("") + head.replace("tri-head", "tri-head second") + rows.slice(10).join("");
    const triBox = $(".tri-box", el);
    $(".tri", el).addEventListener("click", (e) => {
      const li = e.target.closest("li[data-c]");
      if (!li) return;
      const set = picked(r);
      set.has(li.dataset.c) ? set.delete(li.dataset.c) : set.add(li.dataset.c);
      li.classList.toggle("sel");
      li.querySelector(".rank").textContent = set.has(li.dataset.c) ? "✓" : r.trifecta.findIndex(([c]) => c === li.dataset.c) + 1;
      renderPicked(triBox, r);
    });
    renderPicked(triBox, r);
    $(".odds-note", el).textContent = r.odds
      ? (r.odds.at === "確定" || r.odds.at === "締切時"
        ? "オッズ: 締切時オッズ"
        : `オッズ: ${r.odds.at.slice(11, 16)} 時点（最新は公式サイトで確認してください）`)
      : (r.result ? "オッズ未取得" : `オッズは締切${ODDS_WINDOW}分前から自動取得。↻ 更新でいつでも取得できます`);

    if (res) {
      $(".result", el).innerHTML = `
        <h3>結果</h3>
        <div>着順 ${combo(res.order.join("-"))}　3連単 ${yen(res.trifecta_payout)}
          <small class="muted">（予想${res.trifecta_rank ? res.trifecta_rank + "番手" : "21番手以下"}）</small></div>`;
    }
    box.append(el);
  }
}

init().catch((e) => {
  $("#empty").hidden = false;
  $("#empty").textContent = "データの読み込みに失敗しました: " + e.message;
});
