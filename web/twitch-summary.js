/* Compact dashboard view of the same public statistics as Twitch station. */
(function () {
  const get = id => document.getElementById("twitch-summary-" + id);
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
      get("status").textContent = running ? "live" : data.worker.stale ? "stale" : "starting";
      get("status").className = "badge " + (running ? "badge-success" : "badge-error");
      get("balance").textContent = known.length ? format(data.totals.balance) : "--";
      get("change").textContent = known.length ? signed(known.reduce((sum, channel) => sum + channel.balance - channel.baseline, 0)) : "--";
      const bets = data.totals.betting;
      get("profit").textContent = signed(bets.profit) + " points";
      get("results").textContent = `${bets.wins} W / ${bets.losses} L · ${bets.pending} pending`;
      get("channels").textContent = `${data.channels.length} channels tracked · ${live} live · ${bets.bets} confirmed bets`;
    } catch (error) {
      get("status").textContent = "unavailable";
      get("status").className = "badge badge-error";
    }
    setTimeout(refresh, 10000);
  }
  if (get("status")) refresh();
})();
