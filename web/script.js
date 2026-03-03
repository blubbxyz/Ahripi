function setText(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = value;
}

function getStatus(timestamp) {
    if (!timestamp) return "stale";
    const lastUpdate = new Date(timestamp);
    const now = new Date();
    const diffSeconds = (now - lastUpdate) / 1000;
    return diffSeconds < 30 ? "fresh" : "stale";
}

function setBadge(id, status) {
    const el = document.getElementById(id);
    if (!el) return;
    if (status === "fresh") {
        el.textContent = "live";
        el.className = "badge badge-success";
    } else {
        el.textContent = "stale";
        el.className = "badge badge-error";
    }
}

async function update() {
    try {
        const sensors = await fetch("/api/sensors").then(r => r.json());
        const system  = await fetch("/api/system").then(r => r.json());
        const network = await fetch("/api/network").then(r => r.json());
        const fun     = await fetch("/api/fun").then(r => r.json());
        const weather = await fetch("/api/weather").then(r => r.json());

        const systemStatus  = system.timestamp  ? getStatus(system.timestamp)   : "stale";
        const networkStatus = network.timestamp ? getStatus(network.timestamp)  : "stale";
        const sensorsStatus = sensors.timestamp ? getStatus(sensors.timestamp)  : "stale";
        const funStatus     = fun.timestamp     ? getStatus(fun.timestamp)      : "stale";
        const weatherStatus = weather.timestamp ? getStatus(weather.timestamp)  : "stale";

        const camera = await fetch("/api/camera").then(r => r.json());

        setText("cpu",       system.cpu ?? "--");
        setText("ram",       system.ram ?? "--");
        setText("ram_speed", system.ram_speed ?? "--");
        setText("core_temp", system.core_temp ?? "--");

        setText("rx", network.rx_kbps ?? "--");
        setText("tx", network.tx_kbps ?? "--");

        setText("quote",    fun.quote ?? "--");
        setText("insult",   fun.insult ?? "--");
        setText("coinflip", fun.coinflip ?? "--");

        setText("city",                 weather.city ?? "--");
        setText("weather_temp",         (weather.outside_temp ?? "--"));
        setText("weather_condition",    weather.condition ?? "--");
        setText("weather_current_date", weather.current_date ?? "--");

        setText("current_high", (weather.current_high_temp ?? "--"));
        setText("current_low",  (weather.current_low_temp ?? "--"));

        setText("forecast_day1_date", weather.forecast_day1_date ?? "Day 1");
        setText("forecast_day1_temp", (weather.forecast_day1_avg_temp ?? "--"));
        setText("forecast_day1_high", (weather.forecast_day1_high_temp ?? "--"));
        setText("forecast_day1_low",  (weather.forecast_day1_low_temp ?? "--"));

        setText("forecast_day2_date", weather.forecast_day2_date ?? "Day 2");
        setText("forecast_day2_temp", (weather.forecast_day2_avg_temp ?? "--"));
        setText("forecast_day2_high", (weather.forecast_day2_high_temp ?? "--"));
        setText("forecast_day2_low",  (weather.forecast_day2_low_temp ?? "--"));

        // Update backend status badges
        setBadge("system-badge",  systemStatus);
        setBadge("network-badge", networkStatus);
        setBadge("sensors-badge", sensorsStatus);
        setBadge("weather-badge", weatherStatus);
        setBadge("fun-badge",     funStatus);
        setBadge("camera-badge",  camera.ok ? "fresh" : "stale");

        setText("humidity", sensors.humidity ?? "--");
        setText("temp",     sensors.temp ?? "--");
        setText("pressure", sensors.pressure ?? "--");

        setText("camera-last", camera.last_capture ? new Date(camera.last_capture).toLocaleString() : "never");
        setText("camera-status-indicator", camera.ok ? "🟢" : "🔴");
    } catch (error) {
        console.error("Error fetching dashboard data:", error);
    }
}

// ===== COMMENTS =====

async function loadComments() {
  try {
    const list = document.getElementById("commentsList");
    if (!list) return;

    const comments = await fetch("/api/comments").then(r => r.json());
    list.innerHTML = "";

    if (!Array.isArray(comments) || comments.length === 0) {
      const empty = document.createElement("div");
      empty.textContent = "No comments yet.";
      empty.className = "opacity-80";
      list.appendChild(empty);
      return;
    }

    for (const c of comments) {
      const wrap = document.createElement("div");
      wrap.className = "bg-base-300 p-3 rounded-lg mb-2 border border-base-content/20";

      const top = document.createElement("div");
      top.className = "flex justify-between opacity-85 text-sm";

      const name = document.createElement("span");
      name.textContent = c.name || "anon";

      const time = document.createElement("span");
      time.textContent = c.created_at || "";

      top.appendChild(name);
      top.appendChild(time);

      const text = document.createElement("div");
      text.className = "mt-1 whitespace-pre-wrap";
      text.textContent = c.text || "";

      wrap.appendChild(top);
      wrap.appendChild(text);

      if (window.__isAdmin) {
        const actions = document.createElement("div");
        actions.className = "mt-2";

        const del = document.createElement("button");
        del.className = "btn btn-error btn-xs";
        del.textContent = "Delete";

        del.addEventListener("click", async () => {
          if (!confirm("Delete this comment?")) return;
          const res = await fetch(`/api/comments/${c.id}`, { method: "DELETE" });
          if (res.ok) loadComments();
          else alert("Delete failed (not admin?)");
        });

        actions.appendChild(del);
        wrap.appendChild(actions);
      }

      list.appendChild(wrap);
    }
  } catch (e) {
    console.error("loadComments failed:", e);
  }
}

async function postComment() {
  const status = document.getElementById("commentStatus");
  const nameEl = document.getElementById("commentName");
  const textEl = document.getElementById("commentText");
  if (!textEl) return;

  const payload = {
    name: (nameEl?.value || "").trim(),
    text: (textEl.value || "").trim(),
    website: (document.getElementById("website")?.value || "")
  };

  if (!payload.text) {
    if (status) status.textContent = "write something first.";
    return;
  }

  if (status) status.textContent = "posting...";

  try {
    const res = await fetch("/api/comments", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      if (status) status.textContent = data.error || "failed.";
      return;
    }

    textEl.value = "";
    if (status) status.textContent = "posted ✅";
    await loadComments();
  } catch (e) {
    console.error(e);
    if (status) status.textContent = "error.";
  }
}

// ===== ADMIN =====

window.__isAdmin = false;

function closeSidebar() {
  const toggle = document.getElementById("my-drawer-5");
  if (toggle) toggle.checked = false;
}

function openAdminModal() {
  // Close sidebar first so it doesn't cover the modal
  closeSidebar();

  // Small delay to let sidebar close animation finish
  setTimeout(() => {
    document.getElementById("adminPassword").value = "";
    document.getElementById("adminMsg").textContent = "";
    document.getElementById("adminModal").showModal();
    document.getElementById("adminPassword").focus();
  }, 150);
}

function closeAdminModal() {
  document.getElementById("adminModal").close();
}

async function refreshAdminState() {
  try {
    const me = await fetch("/api/admin/me").then(r => r.json());
    window.__isAdmin = !!me.is_admin;
    document.body.classList.toggle("admin-on", window.__isAdmin);

    const loginItem = document.getElementById("adminLoginItem");
    const logoutItem = document.getElementById("adminLogoutItem");
    if (loginItem) loginItem.style.display = window.__isAdmin ? "none" : "";
    if (logoutItem) logoutItem.style.display = window.__isAdmin ? "" : "none";

    loadComments();
  } catch (e) {
    console.error("refreshAdminState failed:", e);
  }
}

async function adminLogin() {
  const msg = document.getElementById("adminMsg");
  const pw = document.getElementById("adminPassword").value;

  msg.textContent = "logging in...";
  const res = await fetch("/api/admin/login", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ password: pw })
  });

  if (!res.ok) {
    msg.textContent = "wrong password.";
    return;
  }

  closeAdminModal();
  await refreshAdminState();
}

async function adminLogout() {
  if (!confirm("Logout of admin session?")) return;
  closeSidebar();
  await fetch("/api/admin/logout", { method: "POST" });
  await refreshAdminState();
}

// ===== CAMERA =====

async function loadLatestPhoto() {
  const img = document.getElementById("latestPhoto");
  if (!img) return;
  const camera = await fetch("/api/camera").then(r => r.json());
  if (camera.latest) {
    img.src = "/photos/" + camera.latest + "?t=" + Date.now();
  }
}

// ===== EVENT LISTENERS (DOM is ready because of defer) =====

document.getElementById("commentSubmit")?.addEventListener("click", postComment);

document.getElementById("adminBtn")?.addEventListener("click", () => {
  if (window.__isAdmin) return; // use the separate logout button instead
  openAdminModal();
});

document.getElementById("adminLogout")?.addEventListener("click", adminLogout);
document.getElementById("adminLogin")?.addEventListener("click", adminLogin);

document.getElementById("adminPassword")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") adminLogin();
});

// ===== INIT =====

refreshAdminState();
loadLatestPhoto();
loadComments();
update();

setInterval(loadComments, 10000);
setInterval(update, 1000);