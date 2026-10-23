"use strict";

/** Twitch Station uses the dashboard's DaisyUI classes and secure admin session.
 * Public values are rendered with textContent; auth codes are fetched only after
 * admin checks. Stats and history have separate polling rates to limit Pi load.
 */

const $ = id => document.getElementById(id);
const number = value => new Intl.NumberFormat().format(value);
const signed = value => (value > 0 ? "+" : "") + number(value);
const date = value => value ? new Date(value * 1000).toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "—";
let isAdmin = false;
let statsVersion = 0;
let historyVersion = 0;
let lastHistory = 0;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

async function api(path, options = {}) {
  const response = await fetch(path, {...options, cache:"no-store", signal:AbortSignal.timeout(10000)});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Request failed.");
  return body;
}

function amount(id, value, sign = false) {
  const element = $(id);
  element.textContent = value === null ? "—" : (sign ? signed(value) : number(value));
  element.classList.toggle("positive", sign && value > 0);
  element.classList.toggle("negative", sign && value < 0);
}

function render(data) {
  const worker = data.worker;
  const running = worker.state === "running" && !worker.stale;
  const labels = {starting:"Starting",needs_auth:"Awaiting Twitch login",running:"Miner online",degraded:"Updates delayed",error:"Retrying",stopping:"Stopping",restarting:"Restarting",validating:"Checking login"};
  $("service-state").textContent = worker.stale ? "Miner not reporting" : (labels[worker.state] || "Waiting for setup");
  $("service-state").className = "badge " + (running ? "badge-success" : "badge-error");
  $("updated").textContent = worker.updated_at ? "Last heartbeat " + date(worker.updated_at) : "No heartbeat yet";
  $("notice").hidden = running;
  $("notice").textContent = worker.stale ? "The miner is not sending fresh updates. Saved statistics remain visible." : (worker.message || "The miner has not reported yet.");
  const known = data.channels.some(channel => channel.balance !== null);
  const net = data.channels.reduce((sum, channel) => sum + (channel.balance === null ? 0 : channel.balance - channel.baseline), 0);
  amount("total-balance", known ? data.totals.balance : null);
  amount("total-net", known ? net : null, true);
  const betting = data.totals.betting;
  amount("bet-profit", betting.bets ? betting.profit : null, true);
  $("bet-count").textContent = number(betting.bets) + " confirmed bets · " + number(betting.pending) + " pending";
  const resolved = betting.wins + betting.losses;
  $("win-rate").textContent = resolved ? Math.round(betting.wins / resolved * 100) + "%" : "—";
  $("win-count").textContent = number(betting.wins) + " wins · " + number(betting.losses) + " losses";
  $("channel-count").textContent = data.channels.length + " channels tracked";
  $("live-count").textContent = data.channels.filter(channel => channel.online && !channel.stale).length + " live";
  $("session-start").textContent = date(worker.started_at);
  amount("today", known ? data.totals.today : null);
  amount("pending", betting.pending);
  $("policy").textContent = data.policy.predictions ? `Paper first · ${number(Math.min(data.policy.bet_max_points, data.learning?.max_stake || 50))} points max` : "Predictions off";

  $("channels").replaceChildren(...data.channels.map(channel => {
    const row = node("tr");
    const nameCell = node("td");
    if (channel.redacted) nameCell.append(node("span", channel.display_name));
    else {
      const link = node("a", channel.name, "link link-hover");
      link.href = "https://www.twitch.tv/" + encodeURIComponent(channel.name);
      link.target = "_blank"; link.rel = "noopener noreferrer";
      nameCell.append(link);
    }
    if (channel.game) nameCell.append(node("span", channel.game, "channel-title"));
    const statusCell = node("td");
    statusCell.append(node("span", channel.stale ? "stale" : (channel.online ? "live" : "offline"), "badge " + (channel.stale ? "badge-error" : channel.online ? "badge-success" : "badge-ghost")));
    const balance = node("td", channel.balance === null ? "—" : number(channel.balance));
    const change = channel.balance === null ? null : channel.balance - channel.baseline;
    const delta = node("td", change === null ? "—" : signed(change), change > 0 ? "positive" : (change < 0 ? "negative" : ""));
    const minutes = channel.watched_minutes;
    row.append(nameCell, statusCell, balance, delta, node("td", minutes >= 60 ? (minutes / 60).toFixed(1) + " h" : Math.floor(minutes) + " min"));
    return row;
  }));

  const current = $("chart-channel").value;
  if (Array.from($("chart-channel").options).map(option => option.value).join(",") !== data.channels.map(channel => channel.name).join(",")) {
    $("chart-channel").replaceChildren(...data.channels.map(channel => { const option = node("option", channel.display_name || channel.name); option.value = channel.name; return option; }));
    if (data.channels.some(channel => channel.name === current)) $("chart-channel").value = current;
  }
  const predictions = data.predictions.map(event => {
    const row = node("tr");
    const first = node("td"); first.append(node("strong", event.channel_display_name || event.channel), node("span", event.title, "channel-title"));
    const state = node("td");
    const closed = ["LOCKED", "RESOLVED", "CANCELED", "CANCELLED"].includes(event.status);
    const paper = event.learning_mode === "paper" && !event.confirmed;
    const label = paper ? (event.trial_outcome ? "Trial: " + event.trial_outcome.toLowerCase() : "Paper trial") : event.result === "WIN" ? "Won" : event.result === "LOSE" ? "Lost" : event.result === "REFUND" ? "Refunded" : event.confirmed ? "Bet placed" : closed ? "Not placed" : event.choice ? "Considering" : "Observing";
    state.append(node("span", label, "badge " + (paper ? "badge-ghost" : event.result === "WIN" ? "badge-success" : event.result === "LOSE" ? "badge-error" : "badge-secondary")));
    if (event.learning_reason) state.title = event.learning_reason;
    row.append(first, node("td", event.trial_choice || event.choice || "—"), node("td", paper ? "0 (simulated)" : event.stake ? number(event.stake) : "—"), state,
      node("td", event.profit === null ? "—" : signed(event.profit), event.profit > 0 ? "positive" : event.profit < 0 ? "negative" : ""), node("td", date(event.updated_at)));
    return row;
  });
  if (!predictions.length) { const row = node("tr"); const cell = node("td", "No predictions observed yet. Bets appear when a channel starts a prediction.", "empty"); cell.colSpan = 6; row.append(cell); predictions.push(row); }
  $("predictions").replaceChildren(...predictions);

  // Trials are shown separately: a simulated return must not inflate real P/L.
  const learningRows = (data.learning?.channels || []).map(channel => {
    const row = node("tr");
    const explanation = node("td", channel.mode === "live" ? "Small stakes eligible" : "Paper trials");
    explanation.append(node("small", channel.reason, "channel-title"));
    if (channel.lower_bound !== null) explanation.append(node("small", `Last lower estimate: ${Math.round(channel.lower_bound*100)}%`, "channel-title"));
    row.append(node("td", channel.display_name || channel.name), node("td", `${channel.trials} / ${data.learning.min_trials} minimum`),
      node("td", `${channel.wins} / ${channel.trials}`), node("td", signed(channel.paper_profit)), explanation);
    return row;
  });
  $("learning").replaceChildren(...learningRows);
}

async function refreshStats() {
  const version = ++statsVersion;
  const data = await api("/api/twitch");
  if (version !== statsVersion) return false;
  render(data);
  return true;
}

async function loadHistory() {
  const version = ++historyVersion;
  const channel = $("chart-channel").value;
  if (!channel) return;
  const data = await api(`/api/twitch/history?channel=${encodeURIComponent(channel)}&hours=${$("period").value}`);
  if (version !== historyVersion) return;
  $("chart").replaceChildren();
  $("chart").toggleAttribute("hidden", data.length < 2);
  $("chart-empty").hidden = data.length >= 2;
  $("chart-empty").textContent = data.length ? "First reading saved. Waiting for the next update…" : "History will appear after the first balance update.";
  $("chart-start").textContent = date(data[0]?.at);
  $("chart-end").textContent = date(data.at(-1)?.at);
  if (data.length < 2) return;
  const low = Math.min(...data.map(point => point.balance));
  const high = Math.max(...data.map(point => point.balance));
  const padding = Math.max(10, (high - low) * 0.12);
  const start = data[0].at, span = Math.max(1, data.at(-1).at - start);
  const points = data.map(point => `${48 + (point.at - start) / span * 578},${195 - (point.balance - low + padding) / (high - low + padding * 2) * 174}`).join(" ");
  const svg = (tag, attrs, text) => {const element = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value); if (text !== undefined) element.textContent = text; return element;};
  for (let index = 0; index < 3; index++) {
    const y = 21 + index * 87;
    $("chart").append(svg("line", {x1:48,x2:626,y1:y,y2:y,class:"chart-grid","stroke-dasharray":"3 5"}));
    $("chart").append(svg("text", {x:0,y:y+4,class:"chart-label"}, number(Math.round(high + padding - index * (high - low + padding * 2) / 2))));
  }
  $("chart").append(svg("polygon", {points:`48,210 ${points} 626,210`,class:"chart-area"}), svg("polyline", {points,fill:"none",class:"chart-line","stroke-width":2,"stroke-linejoin":"round"}));
}

async function loadSettings() {
  const data = await api("/api/twitch/settings");
  if (!isAdmin) return;
  $("username").value = data.username;
  $("channel-list").value = data.channels.join("\n");
  $("enable-predictions").checked = data.predictions;
  $("bet-percentage").value = data.bet_percentage;
  $("bet-max").value = data.bet_max_points;
  $("bet-min").value = data.bet_min_balance;
}

async function loadAuth() {
  if (!isAdmin) return;
  const auth = await api("/api/twitch/auth");
  if (!isAdmin) return;
  $("activation").hidden = !auth.code || auth.expires_at <= Date.now()/1000;
  $("auth-code").textContent = auth.code || "";
  $("auth-message").textContent = auth.message || "";
  $("auth-expires").textContent = auth.expires_at ? "Expires " + date(auth.expires_at) : "";
}

$("admin-button").addEventListener("click", async () => {
  // Close the settings drawer before opening the same modal as the dashboard.
  $("twitch-drawer").checked = false;
  if (isAdmin) { $("settings-panel").hidden = !$("settings-panel").hidden; if (!$("settings-panel").hidden) {await loadSettings(); $("settings-panel").scrollIntoView({behavior:"smooth"});} }
  else { $("login-dialog").showModal(); $("password").focus(); }
});
$("cancel-login").addEventListener("click", () => $("login-dialog").close());
$("login-form").addEventListener("submit", async event => {
  event.preventDefault(); $("login-error").textContent = "";
  try {
    await api("/api/admin/login", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({password:$("password").value})});
    $("password").value = "";
    const me = await api("/api/admin/me");
    if (!me.is_admin) throw new Error("Use the HTTPS dev website so the admin cookie can be saved.");
    isAdmin = true; $("admin-button").textContent = "Settings"; $("login-dialog").close(); $("settings-panel").hidden = false;
    if (await refreshStats()) await loadHistory();
    await loadSettings(); await loadAuth(); $("settings-panel").scrollIntoView({behavior:"smooth"});
  } catch(error) { $("login-error").textContent = error.message; }
});
$("logout").addEventListener("click", async () => {
  await api("/api/admin/logout", {method:"POST"});
  isAdmin = false; ++statsVersion; ++historyVersion;
  $("settings-panel").hidden = true; $("settings-form").reset();
  $("activation").hidden = true; $("auth-code").textContent = "";
  $("auth-message").textContent = ""; $("admin-button").textContent = "Admin Login";
  // Clear private DOM values immediately, even if the visitor refresh fails.
  for (const id of ["channels", "learning", "predictions", "chart-channel", "chart", "notice"]) $(id).replaceChildren();
  try { if (await refreshStats()) await loadHistory(); }
  catch (error) { $("notice").hidden = false; $("notice").textContent = error.message; }
});
$("settings-form").addEventListener("submit", async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    const result = await api("/api/twitch/settings", {method:"POST",headers:{"Content-Type":"application/json","X-Ahripi-Request":"1"},body:JSON.stringify({username:$("username").value,channels:$("channel-list").value.split(/[\n,]+/).map(value=>value.trim()).filter(Boolean),predictions:$("enable-predictions").checked,bet_percentage:Number($("bet-percentage").value),bet_max_points:Number($("bet-max").value),bet_min_balance:Number($("bet-min").value)})});
    $("settings-message").textContent = result.message;
  } catch(error) { $("settings-message").textContent = error.message; }
  finally { button.disabled = false; }
});
for (const id of ["period","chart-channel"]) $(id).addEventListener("change", () => loadHistory().catch(error=>{$("chart-empty").hidden=false; $("chart-empty").textContent=error.message;}));

async function poll() {
  try {
    if (!await refreshStats()) return;
    $("refresh-status").textContent = "Updated " + new Date().toLocaleTimeString();
    if (Date.now() - lastHistory > 30000) {await loadHistory(); lastHistory=Date.now();}
    await loadAuth();
  } catch(error) { $("notice").hidden=false; $("notice").textContent=error.message+" Retrying…"; $("service-state").textContent="unavailable"; $("service-state").className="badge badge-error"; }
  finally { setTimeout(poll, 5000); }
}
api("/api/admin/me").then(async me=>{isAdmin=me.is_admin; if(isAdmin){$("admin-button").textContent="Settings"; await loadAuth();}}).catch(()=>{});
poll();
