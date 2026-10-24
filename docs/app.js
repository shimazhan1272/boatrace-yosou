const $ = (s, el = document) => el.querySelector(s);
const pct = (v) => (v == null ? "-" : (v * 100).toFixed(1) + "%");
const yen = (v) => v.toLocaleString("ja-JP") + "円";
const BET_TYPES = { trifecta: "3連単", exacta: "2連単" };
// "1-2-3" → 艇番カラーの丸数字を「-」でつないだ表示
const combo = (c) => `<span class="combo">${c.split("-").map((n) => `<span class="ball l${n}">${n}</span>`).join('<span class="sep">-</span>')}</span>`;
const ODDS_WINDOW = 25;
// 更新ボタンで直前情報をその場で取得する API（Render）
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
  $("#refresh").onclick = refresh;
  window.addEventListener("hashchange", () => select(parseHash()));
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

// 更新ボタン: 当日のレースは API で直前情報・オッズを取り直して予想し直す。
// それ以外（過去日・API が使えない時）は公開データを読み直す。
async function refresh() {
  const btn = $("#refresh");
  const msg = $("#update-msg");
  btn.disabled = true;
  btn.textContent = "更新中…";
  try {
    if (API_BASE && state.date === todayJST() && state.race) {
      const slow = setTimeout(() => (msg.textContent = "サーバー起動中（最大1分ほどかかります）…"), 4000);
      try {
        const res = await fetch(`${API_BASE}/api/race?date=${state.date}&stadium=${state.stadium}&race=${state.race}`);
        const body = await res.json();
        if (!res.ok) throw new Error(body.detail || res.status);
        const i = day.races.findIndex((r) => r.stadium === state.stadium && r.race_number === state.race);
        if (i >= 0) body.result = day.races[i].result;  // 結果は公開データ側のものを使う
        if (i >= 0) day.races[i] = body; else day.races.push(body);
        await select({ ...state });
        const t = body.live.fetched_at.slice(11, 16);
        const what = body.live.exhibition ? "直前情報" : "出走表のみ（展示前）";
        msg.textContent = body.odds
          ? `${what}・オッズ ${body.odds.at.slice(11, 16)} 取得`
          : `${what} ${t} 取得（締切後のためオッズは更新なし）`;
        return;
      } finally {
        clearTimeout(slow);
      }
    }
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

async function select({ date, stadium, race }) {
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
  $("#updated").textContent = day.backtest ? "" : `最終更新 ${day.updated_at.slice(11, 16)}`;

  const stadiums = [...new Map(day.races.map((r) => [r.stadium, r.stadium_name])).entries()];
  state.stadium = stadiums.some(([n]) => n === stadium) ? stadium : stadiums[0]?.[0];
  const races = day.races.filter((r) => r.stadium === state.stadium);
  state.race = races.some((r) => r.race_number === race) ? race : defaultRace(races);

  const hash = `#${state.date}/${state.stadium}/${state.race}`;
  if (location.hash !== hash) history.replaceState(null, "", hash);
  renderTabs(stadiums, races);
  renderRace(races.find((r) => r.race_number === state.race));
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
    if (r.result?.return > 0 || r.result?.ev_return > 0) b.classList.add("won");
    rnav.append(b);
  }
}

function updateDayButtons() {
  const dates = index.days.map((d) => d.date).sort();
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
    $(".closed", el).textContent = [
      r.stadium_in != null ? `イン1着率 ${pct(r.stadium_in)}` : "",
      r.closed_at ? "締切 " + r.closed_at.slice(11, 16) : "",
    ].filter(Boolean).join("　");

    const maxWin = Math.max(...r.boats.map((b) => b.win));
    $("tbody", el).innerHTML = r.boats.map((b) => `
      <tr>
        <td><span class="lane l${b.boat}">${b.boat}</span></td>
        <td class="name">${b.name}<span class="cls">${b.class}</span></td>
        <td class="num hide-sm">${b.nat_win?.toFixed(2) ?? "-"}</td>
        <td class="num hide-sm">${b.meet_place != null ? `${b.meet_place.toFixed(2)}<small class="muted">(${b.meet_races})</small>` : "-"}</td>
        <td class="num hide-sm">${b.st_exp != null ? "." + Math.round(b.st_exp * 100).toString().padStart(2, "0") : "-"}</td>
        <td class="num hide-sm">${b.ex_time != null ? `${b.ex_time.toFixed(2)}<small class="${b.ex_adj < 0 ? "good" : "muted"}">(${b.ex_adj > 0 ? "+" : ""}${b.ex_adj.toFixed(2)})</small>` : "-"}</td>
        <td><div class="bar"><i style="width:${(b.win / maxWin) * 80}px"></i><small>${pct(b.win)}</small></div></td>
        <td class="num">${pct(b.top2)}</td>
        <td class="num">${pct(b.top3)}</td>
      </tr>`).join("");

    const res = r.result;
    const hitCombos = new Set(res?.trifecta ?? []);
    // 3連単上位20点: 出現確率・オッズ・期待値（= 出現確率 × オッズ）
    const odds = r.odds?.trifecta ?? {};
    const head = `<li class="tri-head"><span class="rank">順</span><span>組番</span><span class="p">確率</span><span class="o">オッズ</span><span class="ev">期待値</span></li>`;
    const rows = r.trifecta.slice(0, 20).map(([c, p], i) => {
      const o = odds[c];
      const ev = o ? p * o : null;
      return `<li class="${hitCombos.has(c) ? "hit" : ""}"><span class="rank">${i + 1}</span>${combo(c)}`
        + `<span class="p">${pct(p)}</span><span class="o">${o ? o.toFixed(1) : "-"}</span>`
        + `<span class="ev ${ev >= 1 ? "plus" : ""}">${ev ? ev.toFixed(2) : "-"}</span></li>`;
    });
    // PC は 1〜10位 / 11〜20位 の2列（各列に見出し）、スマホは1列
    $(".tri", el).innerHTML = head + rows.slice(0, 10).join("") + head.replace("tri-head", "tri-head second") + rows.slice(10).join("");
    $(".odds-note", el).textContent = r.odds
      ? `オッズ: ${r.odds.at === "確定" ? "確定オッズ" : r.odds.at.slice(11, 16) + " 時点"}（最新は公式サイトで確認してください）`
      : (r.result ? "オッズ未取得" : `オッズは締切${ODDS_WINDOW}分前から自動取得。↻ 更新でいつでも取得できます`);

    const ev = r.ev;
    const evList = $(".ev-list", el);
    if (!ev) {
      const closed = r.result || r.closed_at <= new Date(Date.now() + 9 * 3600e3).toISOString().replace("T", " ");
      evList.innerHTML = `<li class="muted">${closed ? "オッズ未取得（対象外）" : `締切${ODDS_WINDOW}分前から表示`}</li>`;
    } else if (!ev.picks.length) {
      evList.innerHTML = `<li class="muted">期待値の高い買い目なし（見送り）</li>`;
    } else {
      const hitSet = new Set([...(res?.trifecta ?? []), res && res.order.slice(0, 2).join("-")]);
      evList.innerHTML = ev.picks.map((p) => `
        <li class="${hitSet.has(p.combo) ? "hit" : ""}">
          <span class="bt">${BET_TYPES[p.type]}</span> ${combo(p.combo)}
          <small class="muted">${p.odds.toFixed(1)}倍 × ${pct(p.prob)} = 期待値 ${p.ev.toFixed(2)}</small>
        </li>`).join("") + `<li class="muted asof">オッズ ${ev.odds_at.slice(11, 16)} 時点</li>`;
    }

    if (res) {
      const hit = res.return > 0;
      $(".result", el).innerHTML = `
        <h3>結果</h3>
        <div>着順 <b>${res.order.join("-")}</b>（3連単 ${yen(res.trifecta_payout)}）</div>
        <div>本命: <span class="${res.honmei_hit ? "hit" : "miss"}">${res.honmei_hit ? "1着" : "外れ"}</span></div>
        <div>推奨${index.tri_bets}点: <span class="${hit ? "hit" : "miss"}">${hit ? "的中 " + yen(res.return) : "外れ"}</span>
          <small class="muted">（予想${res.trifecta_rank ?? "圏外"}番手）</small></div>
        ${res.ev_bet ? `<div>期待値: <span class="${res.ev_return ? "hit" : "miss"}">${res.ev_return ? "的中 " + yen(res.ev_return) : "外れ"}</span>
          <small class="muted">（${yen(res.ev_bet)}購入）</small></div>` : ""}`;
    }
    box.append(el);
  }
}

init().catch((e) => {
  $("#empty").hidden = false;
  $("#empty").textContent = "データの読み込みに失敗しました: " + e.message;
});
