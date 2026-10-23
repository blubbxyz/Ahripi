/**
 * Fetch the same public snapshot used by Twitch Station every ten seconds.
 * Numeric summaries belong to the wide Twitch card; worker health belongs to
 * #twitch-badge in Backend Status. Do not add a second status badge to the card.
 * A fresh heartbeat alone isn't sufficient: the worker must report "running".
 */
(function () {
  const get = id => document.getElementById("twitch-summary-" + id);
  const badge = document.getElementById("twitch-badge");
  const format = value => new Intl.NumberFormat().format(value);
  const signed = value => (value > 0 ? "+" : "") + format(value);
  async function refresh() {
    try {
      const response = await fetch("/api/twitch", {cache: "no-store", signal: AbortSignal.timeout(8000)});
      if (!response.ok) throw new Error("Statistics unavailable");
      const data = await response.json();
      const known = data.channels.filter(channel => channel.balance !== null);
      const live = data.channels.filter(channel => channel.online && !channel.stale).length;
      const running = data.worker.state === "running" && !data.worker.stale;
      const state = data.worker.stale ? "stale" : data.worker.state;
      const labels = {needs_auth: "login needed", degraded: "delayed", error: "retrying", validating: "checking login", restarting: "restarting", stopping: "stopping"};
      badge.textContent = running ? "live" : labels[state] || (state === "starting" ? "starting" : "stale");
      badge.className = "badge " + (running ? "badge-success" : "badge-error");
      get("balance").textContent = known.length ? format(data.totals.balance) : "--";
      get("change").textContent = known.length ? signed(known.reduce((sum, channel) => sum + channel.balance - channel.baseline, 0)) : "--";
      const bets = data.totals.betting;
      get("profit").textContent = signed(bets.profit) + " points";
      get("results").textContent = `${bets.wins} W / ${bets.losses} L · ${bets.pending} pending`;
      get("channels").textContent = `${data.channels.length} channels tracked · ${live} live · ${bets.bets} confirmed bets`;
    } catch (error) {
      // Keep saved numbers visible, but never leave a green badge after failure.
      badge.textContent = "unavailable";
      badge.className = "badge badge-error";
    }
    setTimeout(refresh, 10000);
  }
  if (badge && get("balance")) refresh();
})();
