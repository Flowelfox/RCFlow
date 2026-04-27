"""Browser-based RCFlow worker dashboard.

Used on Linux (and as a portable fallback elsewhere) when the bundled
PyInstaller tcl/tk dashboard is not viable.  Serves a single-page HTML
+ JS dashboard at ``/dashboard`` that talks to the existing REST + WS
API.  ``rcflow gui`` on Linux opens this URL with ``xdg-open`` instead
of launching the broken Tk dashboard.

The HTML route itself is unauthenticated so the browser can fetch it;
all API calls the page makes use the existing ``X-API-Key`` flow.  The
key is passed in via the URL fragment (``#key=...``) so it never lands
in the worker access log or browser history; the page reads it via
``location.hash`` and stores it in-memory only.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RCFlow Worker</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #0f172a; --panel: #1e293b; --border: #334155;
  --text: #e2e8f0; --muted: #94a3b8;
  --accent: #38bdf8; --ok: #4ade80; --warn: #fbbf24; --err: #f87171;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: ui-sans-serif, system-ui, sans-serif;
       background: var(--bg); color: var(--text); }
header { padding: 16px 24px; border-bottom: 1px solid var(--border);
         display: flex; align-items: center; gap: 16px; }
header h1 { margin: 0; font-size: 18px; font-weight: 600; }
header .pill { padding: 4px 10px; border-radius: 999px;
               font-size: 12px; background: #334155; color: var(--text); }
header .pill.ok { background: var(--ok); color: #0f172a; }
header .pill.err { background: var(--err); color: #0f172a; }
header .pill.warn { background: var(--warn); color: #0f172a; }
main { padding: 24px; display: grid; gap: 16px;
       grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
.card { background: var(--panel); border: 1px solid var(--border);
        border-radius: 8px; padding: 16px; }
.card h2 { margin: 0 0 12px; font-size: 14px; font-weight: 600;
           color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
.row { display: flex; justify-content: space-between; padding: 6px 0;
       border-bottom: 1px dashed var(--border); font-size: 14px; }
.row:last-child { border-bottom: 0; }
.row .label { color: var(--muted); }
.row .value { font-family: ui-monospace, monospace; }
button { background: var(--accent); color: #0f172a; border: 0;
         padding: 8px 14px; border-radius: 6px; font-weight: 600;
         cursor: pointer; font-size: 13px; }
button.ghost { background: transparent; color: var(--accent);
               border: 1px solid var(--accent); }
button:disabled { opacity: .5; cursor: not-allowed; }
.token { display: flex; gap: 8px; align-items: center; }
.token code { flex: 1; background: #0f172a; padding: 8px 10px;
              border-radius: 4px; font-size: 12px; overflow: hidden;
              text-overflow: ellipsis; white-space: nowrap; }
#error { background: var(--err); color: #0f172a; padding: 8px 12px;
         border-radius: 6px; margin: 16px 24px 0; display: none; }
#nokey { padding: 24px; }
.hint { color: var(--muted); font-size: 13px; margin-top: 8px; }
</style>
</head>
<body>
<header>
  <h1>RCFlow Worker</h1>
  <span id="status" class="pill">Loading…</span>
  <span style="flex: 1"></span>
  <span class="pill" id="version">v?</span>
</header>
<div id="error"></div>
<div id="nokey" style="display:none">
  <h2>API key required</h2>
  <p>Open this dashboard via <code>rcflow gui</code> so the worker can
  attach the key. Direct access from the address bar will not see the
  authenticated endpoints.</p>
  <p class="hint">If you already know the key you can append
  <code>#key=&lt;your key&gt;</code> to the URL.</p>
</div>
<main id="main" style="display:none">
  <section class="card">
    <h2>Server</h2>
    <div class="row"><span class="label">Bind address</span>
      <span class="value" id="bind">—</span></div>
    <div class="row"><span class="label">Port</span>
      <span class="value" id="port">—</span></div>
    <div class="row"><span class="label">External (UPnP)</span>
      <span class="value" id="upnp">—</span></div>
    <div class="row"><span class="label">External (NAT-PMP)</span>
      <span class="value" id="natpmp">—</span></div>
    <div class="row"><span class="label">Backend ID</span>
      <span class="value" id="backend">—</span></div>
    <div class="row"><span class="label">Active sessions</span>
      <span class="value" id="sessions">0</span></div>
    <div class="row"><span class="label">Worker version</span>
      <span class="value" id="ver2">—</span></div>
  </section>
  <section class="card">
    <h2>API token</h2>
    <div class="token">
      <code id="token-display">••••••••</code>
      <button id="copy-token">Copy</button>
      <button class="ghost" id="reveal-token">Reveal</button>
    </div>
    <p class="hint">Hand this to a client to connect to this worker.</p>
  </section>
  <section class="card" style="grid-column: 1 / -1">
    <h2>Logs</h2>
    <pre id="logs" style="margin: 0; max-height: 300px; overflow: auto;
       background: #0f172a; padding: 12px; border-radius: 6px;
       font-size: 12px; line-height: 1.4;">Streaming…</pre>
  </section>
</main>
<script>
(() => {
  const params = new URLSearchParams(location.hash.slice(1));
  let key = params.get("key") || sessionStorage.getItem("rcflow_dashboard_key");
  if (!key) {
    document.getElementById("nokey").style.display = "block";
    return;
  }
  sessionStorage.setItem("rcflow_dashboard_key", key);
  // Strip the key from the URL bar so a quick screenshare doesn't expose it.
  history.replaceState(null, "", location.pathname);
  document.getElementById("main").style.display = "grid";

  const headers = { "X-API-Key": key };
  const elError = document.getElementById("error");
  const showError = msg => {
    elError.textContent = msg;
    elError.style.display = msg ? "block" : "none";
  };

  async function fetchJson(path) {
    const r = await fetch(path, { headers });
    if (r.status === 401) {
      showError("API key rejected — relaunch via `rcflow gui`.");
      throw new Error("auth");
    }
    if (!r.ok) throw new Error(path + " → " + r.status);
    return r.json();
  }

  async function refresh() {
    try {
      const info = await fetchJson("/api/info");
      showError("");
      // /api/info does not expose the worker's bind/port; derive both
      // from the window URL we were loaded from.  This is always the
      // running worker's listen address since the page is served by the
      // same FastAPI app.
      document.getElementById("bind").textContent = location.hostname;
      document.getElementById("port").textContent = location.port || (location.protocol === "https:" ? 443 : 80);
      document.getElementById("backend").textContent = info.backend_id || "—";
      document.getElementById("sessions").textContent =
        info.active_sessions ?? 0;
      const ver = info.version || "—";
      document.getElementById("version").textContent = "v" + ver;
      document.getElementById("ver2").textContent = ver;
      const upnp = info.upnp;
      document.getElementById("upnp").textContent =
        upnp && upnp.status === "mapped"
          ? upnp.external_ip + ":" + upnp.external_port
          : (upnp ? upnp.status : "disabled");
      const natpmp = info.natpmp;
      document.getElementById("natpmp").textContent =
        natpmp && natpmp.status === "mapped"
          ? natpmp.public_ip + ":" + natpmp.external_port
          : (natpmp ? natpmp.status : "disabled");
      const status = document.getElementById("status");
      status.textContent = "Running";
      status.className = "pill ok";
    } catch (err) {
      const status = document.getElementById("status");
      status.textContent = "Unreachable";
      status.className = "pill err";
    }
  }

  document.getElementById("copy-token").addEventListener("click", () => {
    navigator.clipboard.writeText(key).then(() => {
      const btn = document.getElementById("copy-token");
      const original = btn.textContent;
      btn.textContent = "Copied";
      setTimeout(() => { btn.textContent = original; }, 1500);
    });
  });
  document.getElementById("reveal-token").addEventListener("click", () => {
    const display = document.getElementById("token-display");
    const btn = document.getElementById("reveal-token");
    if (display.textContent === key) {
      display.textContent = "••••••••";
      btn.textContent = "Reveal";
    } else {
      display.textContent = key;
      btn.textContent = "Hide";
    }
  });

  refresh();
  setInterval(refresh, 4000);
})();
</script>
</body>
</html>
"""


@router.get(
    "/dashboard",
    summary="Worker browser dashboard",
    description=(
        "Serves the single-page HTML dashboard used as the Linux worker GUI. "
        "Authenticated API calls made by the page receive the key via the URL "
        "fragment so it never reaches the access log."
    ),
    response_class=HTMLResponse,
)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(_DASHBOARD_HTML)
