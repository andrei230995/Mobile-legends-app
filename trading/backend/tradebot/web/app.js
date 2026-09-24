"use strict";
// Tradebot control panel. All server text is escaped before insertion. The session
// lives in an HttpOnly cookie; only the CSRF token is kept in memory (never storage).

let CSRF = null;
let SESSION = null;
let STATE = null;
let timer = null;

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const gbp = (v) => (v === null || v === undefined || v === "" ? "–" : (Number(v) < 0 ? "−£" : "£") + Math.abs(Number(v)).toFixed(2));
const cls = (v) => (Number(v) > 0 ? "good" : Number(v) < 0 ? "bad" : "");
const ago = (iso) => {
  if (!iso || !STATE) return "–";
  const s = (new Date(STATE.server_time) - new Date(iso)) / 1000;
  if (s < 90) return `${Math.round(s)}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
};
const when = (iso) => (iso ? new Date(iso).toLocaleString("en-GB", { timeZone: "Europe/London", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "–");

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  if (CSRF) headers["X-CSRF-Token"] = CSRF;
  const r = await fetch(path, { credentials: "same-origin", ...opts, headers });
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("login required"); }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = body.detail;
    throw new Error(typeof d === "string" ? d : JSON.stringify(d ?? body));
  }
  return body;
}

function showLogin() {
  clearInterval(timer);
  $("app").classList.add("hidden");
  $("login").classList.remove("hidden");
}

async function boot() {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  SESSION = await api("/api/session");
  $("totp-row").classList.toggle("hidden", !SESSION.totp_required);
  ["limits-totp-row", "live-totp-row"].forEach((id) => $(id).classList.toggle("hidden", !SESSION.totp_required));
  if (!SESSION.login_configured) {
    $("login-error").textContent = "Login is disabled until TRADEBOT_ADMIN_PASSWORD_HASH is set on the server.";
  }
  if (SESSION.authenticated) { CSRF = SESSION.csrf; startApp(); } else showLogin();
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("login-error").textContent = "";
  try {
    const r = await api("/api/login", { method: "POST", body: JSON.stringify({ password: $("pw").value, totp: $("totp").value || null }) });
    CSRF = r.csrf;
    $("pw").value = ""; $("totp").value = "";
    startApp();
  } catch (err) { $("login-error").textContent = err.message; }
});

function startApp() {
  $("login").classList.add("hidden");
  $("app").classList.remove("hidden");
  refresh();
  clearInterval(timer);
  timer = setInterval(refresh, 15000);
}

document.addEventListener("visibilitychange", () => { if (!document.hidden && CSRF) refresh(); });

$("tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (!b) return;
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== `tab-${b.dataset.tab}`));
  if (b.dataset.tab === "decisions") loadDecisions();
  if (b.dataset.tab === "reports") loadReports();
  if (b.dataset.tab === "system") loadEvents();
});

async function refresh() {
  try { STATE = await api("/api/state"); render(); }
  catch (err) { $("banner").textContent = `Cannot reach server: ${err.message}`; $("banner").className = "banner bad"; }
}

function kv(k, v, c = "") { return `<div class="kv"><div class="k">${esc(k)}</div><div class="v ${c}">${v}</div></div>`; }

function render() {
  const s = STATE;
  const mode = s.mode || "?";
  $("mode-pill").textContent = mode.replace("_", "-").toUpperCase();
  $("mode-pill").className = `pill ${mode === "live" ? "live" : mode === "paper" ? "paper" : ""}`;
  const on = s.trading_enabled && !s.entries_paused && !s.halted_reason;
  $("trading-pill").textContent = s.halted_reason ? "HALTED" : s.entries_paused ? "ENTRIES PAUSED" : s.trading_enabled ? "TRADING ON" : "TRADING OFF";
  $("trading-pill").className = `pill ${on ? "on" : ""}`;
  $("health-dot").className = `dot ${s.health.healthy ? "ok" : "bad"}`;
  const a = s.account;
  $("equity").textContent = gbp(a.equity_gbp);
  $("equity-sub").textContent = a.equity ? `${Number(a.equity).toFixed(2)} ${a.currency} · cash ${Number(a.cash).toFixed(2)} ${a.currency} · FX ${a.fx_to_gbp} · ${ago(a.as_of)}` : "No account data yet";

  const warnings = [];
  if (s.halted_reason) warnings.push(`Halted: ${s.halted_reason}`);
  if (s.entries_paused_reason) warnings.push(`Entries paused: ${s.entries_paused_reason.replace("_", " ")}`);
  Object.keys(s.alerts || {}).forEach((k) => warnings.push(`Alert: ${k}`));
  if (s.clock === "replay") warnings.push("DEMO: replayed historical data on a simulated clock — not live markets.");
  if (!s.broker.status || !s.broker.status.ok) warnings.push("Broker connection problem");
  $("banner").innerHTML = warnings.map(esc).join("<br>");
  $("banner").className = warnings.length ? `banner ${s.halted_reason || !s.health.healthy ? "bad" : ""}` : "banner hidden";

  const t = s.today;
  if (t && t.trading_pnl_gbp !== undefined) {
    $("today-grid").innerHTML = [
      kv("Trading P&L", gbp(t.trading_pnl_gbp), cls(t.trading_pnl_gbp)),
      kv("Net after costs", gbp(t.net_after_operating_costs_gbp), cls(t.net_after_operating_costs_gbp)),
      kv("Realised", gbp(t.realised_pnl_gbp), cls(t.realised_pnl_gbp)),
      kv("Unrealised change", gbp(t.unrealised_change_gbp), cls(t.unrealised_change_gbp)),
      kv("Open unrealised", gbp(t.unrealised_open_gbp), cls(t.unrealised_open_gbp)),
      kv("Broker fees", gbp(t.broker_fees_gbp)),
      kv("Operating costs (pro-rata)", gbp(t.operating_costs_gbp.total)),
      kv("Deposits / withdrawals", gbp(t.deposits_withdrawals_gbp)),
      kv("FX / other", gbp(t.fx_and_other_residual_gbp)),
      kv("Drawdown", `${esc(s.risk.drawdown_pct)}%`),
    ].join("");
    $("today-note").textContent = `Since ${when(t.window_utc[0])}. Deposits are not profit; open losing positions are included.`;
  } else {
    $("today-grid").innerHTML = "";
    $("today-note").textContent = t ? t.status : "Waiting for the first account snapshot.";
  }

  $("strategies").innerHTML = s.strategies.map((st) => `
    <div class="pos"><div class="row"><b>${esc(st.key)}</b><span class="tag ${esc(st.status)}">${esc(st.status.replace("_", " "))}</span></div>
    <div class="muted small">params ${esc(JSON.stringify(st.params))} · validated avg trade ${esc(st.expected_edge_bps)} bps net ·
      forward paper ${esc(st.forward_paper.sessions)} sessions / ${esc(st.forward_paper.closed_trades)} trades</div>
    ${st.failed_checks.length ? `<div class="small warn-text">Not live-eligible: ${st.failed_checks.map(esc).join(", ")}</div>` : ""}</div>`).join("") || "<p class='muted'>No strategies registered.</p>";

  $("recent-decisions").innerHTML = s.decisions.slice(0, 8).map(decisionItem).join("") || "<li class='muted'>No decisions yet.</li>";

  $("positions").innerHTML = s.positions.map((p) => p.error ? `<p class="bad">${esc(p.error)}</p>` : `
    <div class="pos"><div class="row"><b>${esc(p.symbol)}</b><span>${esc(Number(p.value).toFixed(2))} ${esc(p.currency)}</span></div>
    <div class="muted small">${esc(p.qty)} @ avg ${esc(Number(p.avg_price).toFixed(2))} · last ${esc(Number(p.price).toFixed(2))} · ${esc(p.strategy)}</div>
    <div class="small">Stop ${esc(p.stop_price ?? "none")} (${p.stop_mode === "broker" ? "held at broker" : p.stop_mode === "software" ? "software-managed — needs the server running" : "none"})
      ${p.exit_pending ? `<br><span class="warn-text">Exit pending: ${esc(p.exit_pending)}</span>` : ""}</div>
    <button class="btn danger wide" data-close="${esc(p.symbol)}">Close ${esc(p.symbol)}</button></div>`).join("")
    || "<p class='muted'>No open positions — cash.</p>";
  $("pending").innerHTML = s.pending_orders.map((o) => `<div class="pos"><div class="row"><b>${esc(o.side)} ${esc(o.symbol)}</b><span class="tag">${esc(o.status)}</span></div>
    <div class="muted small">${esc(o.purpose)} · ${esc(o.type)} · qty ${esc(o.qty)} ${o.stop_price ? "· stop " + esc(o.stop_price) : ""} · filled ${esc(o.filled_qty)} · ${when(o.created_at)}</div></div>`).join("")
    || "<p class='muted'>No working orders.</p>";

  const r = s.risk;
  $("risk-budget").innerHTML = [
    kv("Today", `${esc(r.day_pnl_pct)}%`, cls(r.day_pnl_pct)),
    kv("Daily loss budget left", `${esc(r.daily_loss_budget_remaining_pct)}%`),
    kv("Drawdown", `${esc(r.drawdown_pct)}%`),
    kv("Drawdown budget left", `${esc(r.drawdown_budget_remaining_pct)}%`),
  ].join("");
  $("limits-version").textContent = `v${r.version}${r.confirmed ? " · confirmed" : " · not confirmed"}`;
  if (!document.activeElement || !$("limits-form").contains(document.activeElement)) renderLimits(r.limits);

  const d = s.data || { symbols: {} };
  $("connections").innerHTML = `
    <p class="small">Broker: <b>${esc(s.broker.name)}</b> ${s.broker.paper ? "(paper)" : "(LIVE)"} —
      ${s.broker.status && s.broker.status.ok ? `<span class="good">connected ${ago(s.broker.status.at)}</span>` : `<span class="bad">${esc(s.broker.status ? s.broker.status.error : "unknown")}</span>`}</p>
    <p class="small">Engine heartbeat: ${ago(s.health.heartbeat)} ${s.health.last_error ? `<span class="bad">${esc(s.health.last_error)}</span>` : ""}</p>
    <p class="small">Market data: <b>${esc(d.provider)}</b></p>
    <ul class="list small">${Object.entries(d.symbols || {}).map(([k, v]) => `<li>${esc(k)}: ${v.ok ? `bid ${esc(v.bid)} / ask ${esc(v.ask)} · spread ${esc(v.spread_bps)}bps · age ${esc(v.age_s)}s · ${esc(v.source)}/${esc(v.feed)}${v.delayed ? " · DELAYED" : ""}${v.replay ? " · REPLAY" : ""}` : `<span class="bad">${esc(v.error || "invalid")}</span>`}</li>`).join("")}</ul>
    <p class="small">FX: ${s.fx ? (s.fx.ok ? esc(`${s.fx.rate} (${s.fx.source})`) : `<span class="bad">${esc(s.fx.error)}</span>`) : "–"}</p>
    <p class="small">Reconciliation: ${s.reconciliation ? (s.reconciliation.ok ? '<span class="good">broker and bot agree</span>' : `<span class="bad">${esc(s.reconciliation.issues.join("; "))}</span>`) : "–"}</p>
    <p class="small">AI news assessment: ${s.ai.enabled ? esc(`${s.ai.mode} (${s.ai.model})`) : "disabled"}</p>
    ${s.broker.notes.map((n) => `<p class="muted small">${esc(n)}</p>`).join("")}`;
  $("missing").innerHTML = s.missing_configuration.map((m) => `<li>${esc(m)}</li>`).join("") || "<li class='good'>Nothing missing.</li>";
  $("notifications").innerHTML = s.notifications.map((n) => `<li><b>${esc(n.title)}</b> <span class="muted">${when(n.ts)} · ${esc(n.delivered)}</span><br>${esc(n.body)}</li>`).join("") || "<li class='muted'>None yet.</li>";
  $("live-blockers").innerHTML = s.live_blockers.map((b) => `<li class="bad">✗ ${esc(b)}</li>`).join("") || "<li class='good'>No blockers.</li>";
  $("footer").textContent = `Server time ${when(s.server_time)} (UK) · ${s.clock === "replay" ? "SIMULATED CLOCK" : "live clock"}`;
}

function decisionItem(d) {
  return `<li><div class="row"><span><b>${esc(d.symbol || "")}</b> ${esc(d.action)} · <span class="muted small">${esc(d.strategy || "")}</span></span><span class="tag ${esc(d.outcome)}">${esc(d.outcome.replace("_", " "))}</span></div>
    <div class="small">${esc(d.summary)}</div><div class="muted small">${when(d.ts)}</div>
    ${d.checks && d.checks.length ? `<details><summary class="small">checks</summary><ul class="checks">${d.checks.map((c) => `<li class="${c.passed ? "good" : "bad"}">${c.passed ? "✓" : "✗"} ${esc(c.name)} <span class="muted">${esc(c.detail)}</span></li>`).join("")}</ul>
    ${d.evidence ? `<pre class="mono">${esc(JSON.stringify(d.evidence, null, 1))}</pre>` : ""}</details>` : ""}</li>`;
}

async function loadDecisions() {
  const rows = await api("/api/decisions?limit=100");
  $("decisions").innerHTML = rows.map(decisionItem).join("") || "<li class='muted'>No decisions yet.</li>";
}

async function loadReports() {
  const rows = await api("/api/reports");
  $("reports").innerHTML = rows.map((r) => r.trading_pnl_gbp === undefined ? `<details><summary>${esc(r.date)} — ${esc(r.status)}</summary></details>` : `
    <details><summary><b>${esc(r.date)}</b> · <span class="${cls(r.net_after_operating_costs_gbp)}">${gbp(r.net_after_operating_costs_gbp)} net</span> · ${esc(r.mode)}</summary>
    <table class="rep">
      <tr><td>Equity start → end</td><td>${gbp(r.equity_start_gbp)} → ${gbp(r.equity_end_gbp)}</td></tr>
      <tr><td>Deposits / withdrawals (excluded)</td><td>${gbp(r.deposits_withdrawals_gbp)}</td></tr>
      <tr><td>Trading P&amp;L</td><td>${gbp(r.trading_pnl_gbp)}</td></tr>
      <tr><td>· realised (net of fees)</td><td>${gbp(r.realised_pnl_gbp)}</td></tr>
      <tr><td>· change in unrealised</td><td>${gbp(r.unrealised_change_gbp)}</td></tr>
      <tr><td>· dividends / interest / cash fees</td><td>${gbp(r.dividends_interest_cash_fees_gbp)}</td></tr>
      <tr><td>· FX / other residual</td><td>${gbp(r.fx_and_other_residual_gbp)}</td></tr>
      <tr><td>Broker fees (included above)</td><td>${gbp(r.broker_fees_gbp)}</td></tr>
      <tr><td>Operating costs (server/data/AI)</td><td>${gbp(r.operating_costs_gbp.total)}</td></tr>
      <tr><td><b>Net after operating costs</b></td><td><b>${gbp(r.net_after_operating_costs_gbp)}</b></td></tr>
      <tr><td>Drawdown from peak</td><td>${esc(r.drawdown_from_peak_pct)}%</td></tr>
      <tr><td>Open positions</td><td>${esc(r.outstanding_exposure.map((p) => p.symbol).join(", ") || "none")}</td></tr>
      <tr><td>Operational problems</td><td>${esc(r.operational_problems.length)}</td></tr>
    </table>
    ${r.significant_trades.length ? `<p class="small">Trades: ${r.significant_trades.map((x) => `${esc(x.symbol)} ${gbp(x.realised_gbp)} (${esc(x.return_pct)}%)`).join(", ")}</p>` : ""}
    ${r.operational_problems.length ? `<ul class="list small">${r.operational_problems.map((p) => `<li>${when(p.ts)} ${esc(p.level)}: ${esc(p.message)}</li>`).join("")}</ul>` : ""}
    <p class="muted small">${esc(r.data_provider)} · ${esc(r.broker)}</p></details>`).join("") || "<p class='muted'>No reports yet.</p>";
}

async function loadEvents() {
  const rows = await api("/api/events?limit=80");
  $("events").innerHTML = rows.map((e) => `<li><span class="${e.level === "error" || e.level === "critical" ? "bad" : e.level === "warning" ? "warn-text" : "muted"}">${when(e.ts)} ${esc(e.level)}</span> [${esc(e.category)}] ${esc(e.message)}</li>`).join("");
}

const LIMIT_FIELDS = [
  ["max_position_pct", "Max position % of equity"], ["max_positions", "Max positions"],
  ["max_total_exposure_pct", "Max total exposure % (≤100)"], ["risk_per_trade_pct", "Risk per trade %"],
  ["daily_loss_limit_pct", "Daily loss limit %"], ["max_drawdown_pct", "Max drawdown %"],
  ["max_entries_per_day", "Max entries / day"], ["max_orders_per_day", "Max orders / day"],
  ["max_daily_turnover_pct", "Max daily turnover %"], ["max_spread_bps", "Max spread (bps)"],
  ["max_slippage_bps", "Slippage tolerance (bps)"], ["max_quote_age_seconds", "Max quote age (s)"],
  ["max_cost_to_edge_ratio", "Max cost ÷ edge"], ["cash_buffer_pct", "Cash buffer %"],
  ["report_time_local", "Daily report time (UK)"], ["symbols", "Symbols (comma separated)"],
];

function renderLimits(l) {
  const f = LIMIT_FIELDS.map(([k, label]) => `<label>${esc(label)}<input name="${k}" value="${esc(Array.isArray(l[k]) ? l[k].join(",") : l[k])}"></label>`).join("");
  $("limits-form").innerHTML = f + `
    <label>On drawdown breach<select name="on_drawdown_breach">
      <option value="pause_entries" ${l.on_drawdown_breach === "pause_entries" ? "selected" : ""}>Halt entries, keep managing</option>
      <option value="flatten" ${l.on_drawdown_breach === "flatten" ? "selected" : ""}>Close all positions</option></select></label>
    <label>Close everything before the US close<select name="flat_by_close">
      <option value="false" ${!l.flat_by_close ? "selected" : ""}>No — overnight holds allowed</option>
      <option value="true" ${l.flat_by_close ? "selected" : ""}>Yes — flat every day</option></select></label>`;
}

function collectLimits() {
  const out = {};
  new FormData($("limits-form")).forEach((v, k) => {
    if (k === "symbols") out[k] = v.split(",").map((x) => x.trim()).filter(Boolean);
    else if (k === "flat_by_close") out[k] = v === "true";
    else if (k === "report_time_local" || k === "on_drawdown_breach") out[k] = v;
    else out[k] = Number(v);
  });
  return out;
}

async function saveLimits(confirmOnly) {
  $("limits-msg").textContent = "";
  try {
    const r = await api("/api/risk-limits", { method: "PUT", body: JSON.stringify({
      limits: confirmOnly ? {} : collectLimits(), confirm_only: confirmOnly,
      password: $("limits-pw").value || null, totp: $("limits-totp").value || null }) });
    $("limits-msg").textContent = `Saved as version ${r.version}${r.loosened.length ? " (loosened: " + r.loosened.join(", ") + ")" : ""}.`;
    $("limits-msg").className = "small good";
    $("limits-pw").value = "";
    refresh();
  } catch (err) { $("limits-msg").textContent = err.message; $("limits-msg").className = "small bad"; }
}
$("save-limits").addEventListener("click", () => saveLimits(false));
$("confirm-limits").addEventListener("click", () => saveLimits(true));

document.addEventListener("click", async (e) => {
  const cmd = e.target.closest("[data-cmd]");
  const close = e.target.closest("[data-close]");
  const mode = e.target.closest("[data-mode]");
  try {
    if (cmd) {
      if (cmd.dataset.confirm && !confirm(cmd.dataset.confirm)) return;
      const r = await api(`/api/control/${cmd.dataset.cmd}`, { method: "POST", body: "{}" });
      if (r.note) alert(r.note);
      if (r.results) alert(Object.entries(r.results).map(([k, v]) => `${k}: ${v}`).join("\n") || "Nothing to close.");
      refresh();
    } else if (close) {
      if (!confirm(`Close ${close.dataset.close} at market?`)) return;
      const r = await api("/api/control/close_position", { method: "POST", body: JSON.stringify({ symbol: close.dataset.close }) });
      alert(Object.entries(r.results).map(([k, v]) => `${k}: ${v}`).join("\n"));
      refresh();
    } else if (mode) {
      await api("/api/mode", { method: "POST", body: JSON.stringify({ mode: mode.dataset.mode }) });
      refresh();
    }
  } catch (err) { alert(err.message); }
});

$("go-live").addEventListener("click", () => $("live-panel").classList.toggle("hidden"));
$("live-submit").addEventListener("click", async () => {
  $("live-msg").textContent = "";
  try {
    await api("/api/mode", { method: "POST", body: JSON.stringify({ mode: "live", password: $("live-pw").value, totp: $("live-totp").value || null, confirm: $("live-confirm").value }) });
    $("live-msg").textContent = "Live trading authorised."; $("live-msg").className = "small good";
    refresh();
  } catch (err) { $("live-msg").textContent = err.message; $("live-msg").className = "small bad"; }
  $("live-pw").value = "";
});

$("logout").addEventListener("click", async () => { await api("/api/logout", { method: "POST", body: "{}" }).catch(() => {}); CSRF = null; showLogin(); });

boot().catch((err) => { $("login-error").textContent = err.message; showLogin(); });
