const $ = (s, el = document) => el.querySelector(s);
const pct = (v) => (v == null ? "-" : (v * 100).toFixed(1) + "%");
const yen = (v) => v.toLocaleString("ja-JP") + "円";
const BET_TYPES = { trifecta: "3連単", exacta: "2連単" };
const ODDS_WINDOW = 25;

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
  window.addEventListener("hashchange", () => select(parseHash()));
  const m = index.models.full || index.models.base;
  if (m) {
    $("#model-info").textContent =
      `モデル学習: ${m.trained_at.slice(0, 10)}（期間 ${m.period[0]}〜${m.period[1]}）`;
  }
  renderSummary();
  if (index.days.length) await select(parseHash());
  else $("#empty").hidden = false;
}

// データのある前後の日へ移動する
function stepDay(dir) {
  const dates = index.days.map((d) => d.date).sort();
  const i = dates.indexOf(state.date);
  const next = dates[i + dir];
  if (next) select({ date: next, stadium: state.stadium, race: null });
}

function renderSummary() {
  $("#summary").innerHTML = summaryStats(index.days.filter((d) => !d.backtest));
  $("#summary-bt").innerHTML = summaryStats(index.days.filter((d) => d.backtest));
}

function summaryStats(days) {
  const t = days.reduce(
    (a, d) => ({
      scored: a.scored + d.scored, honmei: a.honmei + d.honmei_hits, tri: a.tri + d.trifecta_hits, bet: a.bet + d.bet, ret: a.ret + d.return,
      evRaces: a.evRaces + (d.ev_races ?? 0), evBet: a.evBet + (d.ev_bet ?? 0), evRet: a.evRet + (d.ev_return ?? 0),
    }),
    { scored: 0, honmei: 0, tri: 0, bet: 0, ret: 0, evRaces: 0, evBet: 0, evRet: 0 }
  );
  const stats = [
    ["レース数", t.scored ? `${t.scored.toLocaleString()}<small>（${days.length}日）</small>` : "0"],
    ["本命 1着率", t.scored ? pct(t.honmei / t.scored) : "-"],
    [`3連単 ${index.tri_bets}点 的中率`, t.scored ? pct(t.tri / t.scored) : "-"],
    [`3連単 ${index.tri_bets}点 回収率`, t.bet ? pct(t.ret / t.bet) : "-"],
    ["期待値買い 回収率", t.evBet ? `${pct(t.evRet / t.evBet)}<small>（${t.evRaces}R）</small>` : "-"],
  ];
  return stats.map(([k, v]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join("");
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
        <td class="num">${pct(b.top3)}</td>
      </tr>`).join("");

    const res = r.result;
    const hitCombos = new Set(res?.trifecta ?? []);
    $(".tri", el).innerHTML = r.trifecta.slice(0, index.tri_bets)
      .map(([c, p]) => `<li class="${hitCombos.has(c) ? "hit" : ""}">${c} <small class="muted">${pct(p)}</small></li>`).join("");

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
          <span class="bt">${BET_TYPES[p.type]}</span> <b>${p.combo}</b>
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
