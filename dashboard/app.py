#!/usr/bin/env python3
from flask import Flask, jsonify, request, render_template_string, Response, redirect, make_response, session
import subprocess, os, time, re, json, base64, socket, uuid, secrets
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
STATUS_CACHE = {}
CLIENT_COUNTER_LAST_SETUP = 0

LAST_CPU = {"total": None, "idle": None}
RUN_TRAFFIC_STATE = "/run/vpn-dashboard-traffic.json"
PERSIST_DIR = "/var/lib/protonpi-dashboard"
PERSIST_TRAFFIC_STATE = f"{PERSIST_DIR}/traffic-monthly.json"
SETTINGS_FILE = "/etc/protonvpn-profiles/dashboard-settings.json"
CLIENT_NAMES_FILE = "/etc/protonvpn-profiles/client-names.json"
CLIENT_POLICIES_FILE = "/etc/protonvpn-profiles/client-policies.json"
DASHBOARD_BACKUP = "/opt/vpn-dashboard/app.py.localbackup"
BACKUP_DIR = "/var/lib/protonpi-dashboard/backups"
AUTH_FILE = "/etc/protonvpn-profiles/dashboard-auth.json"

HTML = r"""
<!doctype html>
<html data-theme="blue">
<head>
  <title>ProtonPi</title>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <style>
    :root {
      --bg0:#05070d; --bg1:#070a12; --card:#111827; --card2:#182236;
      --text:#f4f7fb; --muted:#91a0b6; --line:rgba(255,255,255,.10);
      --primary:#4f8cff; --primary2:#7aa7ff; --good:#35d07f;
      --warn:#ffd166; --bad:#ff5b6e; --shadow:rgba(0,0,0,.33);
    }
    html[data-theme="green"] {
      --bg0:#03100b; --bg1:#06160f; --card:#0e241a; --card2:#133222;
      --primary:#35d07f; --primary2:#7ff0b1;
    }
    html[data-theme="purple"] {
      --bg0:#090514; --bg1:#10091f; --card:#1b1430; --card2:#291d45;
      --primary:#9b6cff; --primary2:#c6a8ff;
    }
    html[data-theme="red"] {
      --bg0:#140507; --bg1:#1d080c; --card:#30141a; --card2:#421b24;
      --primary:#ff5b6e; --primary2:#ff9aa6;
    }

    * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
    body {
      margin:0; color:var(--text);
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
      background:
        radial-gradient(circle at top left, color-mix(in srgb, var(--primary) 30%, transparent), transparent 38%),
        radial-gradient(circle at top right, color-mix(in srgb, var(--primary2) 24%, transparent), transparent 34%),
        linear-gradient(180deg,var(--bg1),var(--bg0));
      padding:12px; padding-bottom:30px;
    }
    .app { max-width:900px; margin:0 auto; }
    .top { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:8px 2px 14px; }
    h1 { margin:0; font-size:29px; letter-spacing:-.9px; }
    h2 { margin:0 0 12px; font-size:17px; letter-spacing:-.2px; }
    .sub { color:var(--muted); font-size:13px; margin-top:3px; }
    .pill {
      padding:8px 11px; border-radius:999px; font-size:13px; font-weight:850;
      color:var(--good); background:color-mix(in srgb, var(--good) 14%, transparent);
      border:1px solid color-mix(in srgb, var(--good) 35%, transparent);
      white-space:nowrap;
    }
    .card {
      background:linear-gradient(180deg,color-mix(in srgb,var(--card2) 92%, transparent),color-mix(in srgb,var(--card) 96%, transparent));
      border:1px solid var(--line); border-radius:23px; padding:15px; margin-bottom:12px;
      box-shadow:0 14px 34px var(--shadow);
    }
    .hero {
      background:
        linear-gradient(135deg,color-mix(in srgb,var(--primary) 25%, transparent),color-mix(in srgb,var(--primary2) 12%, transparent)),
        linear-gradient(180deg,var(--card2),var(--card));
    }
    .label { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.08em; }
    .big { font-size:24px; font-weight:950; margin-top:5px; word-break:break-word; }
    .small { color:var(--muted); font-size:12.5px; line-height:1.35; }
    .grid { display:grid; grid-template-columns:1fr 1fr; gap:9px; }
    .grid3 { display:grid; grid-template-columns:repeat(3,1fr); gap:9px; }
    .stat {
      background:rgba(255,255,255,.045); border:1px solid rgba(255,255,255,.075);
      border-radius:16px; padding:12px; min-height:76px;
    }
    .stat.clickable { cursor:pointer; }
    .stat.clickable:active { transform:scale(.985); }
    .value { font-size:17px; font-weight:900; margin-top:7px; word-break:break-word; }
    .profiles { display:grid; grid-template-columns:1fr 1fr; gap:9px; }
    button, select, input, a.btn {
      border:0; border-radius:16px; padding:13px 10px; color:white; font-weight:900;
      font-size:14px; cursor:pointer; min-height:48px; text-align:center; text-decoration:none;
      max-width:100%;
    }
    input, select {
      width:100%; max-width:100%; min-width:0;
      background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.12);
      color:white;
    }
    input[type="time"] {
      appearance:none;
      -webkit-appearance:none;
      padding-left:10px;
      padding-right:10px;
    }
    button:active, a.btn:active { transform:scale(.98); }
    .gaming { background:linear-gradient(135deg,#2f80ff,#5aa2ff); }
    .p2p { background:linear-gradient(135deg,#13a664,#35d07f); }
    .streaming { background:linear-gradient(135deg,#00a6ff,#00d4ff); }
    .maxsec { background:linear-gradient(135deg,#7c4dff,#b085ff); }
    .off { background:linear-gradient(135deg,#c7384a,#ff5b6e); }
    .ghost { background:rgba(255,255,255,.075); border:1px solid rgba(255,255,255,.12); }
    .danger { background:rgba(255,91,110,.16); border:1px solid rgba(255,91,110,.35); color:#ffb3bc; }
    .warning {
      background:rgba(255,209,102,.14); border:1px solid rgba(255,209,102,.35);
      color:#ffe3a3; border-radius:16px; padding:12px; margin-bottom:12px; font-weight:850;
    }
    .badwarn {
      background:rgba(255,91,110,.15); border:1px solid rgba(255,91,110,.35);
      color:#ffb3bc; border-radius:16px; padding:12px; margin-bottom:12px; font-weight:850;
    }

    .graphBox {
      width:100%; height:270px; position:relative; border-radius:18px;
      background:
        linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.015)),
        rgba(0,0,0,.20);
      border:1px solid rgba(255,255,255,.07); overflow:hidden;
    }
    canvas { display:block; width:100%; height:100%; touch-action:none; }
    .legend { display:flex; justify-content:space-between; gap:12px; margin-top:10px; color:var(--muted); font-size:13px; }
    .dot { width:9px; height:9px; display:inline-block; border-radius:50%; margin-right:6px; }
    .rx { background:var(--primary); } .tx { background:var(--good); }
    .hidden { display:none !important; }
    .list { display:flex; flex-direction:column; gap:8px; }
    .client { background:rgba(255,255,255,.045); padding:10px; border-radius:13px; border:1px solid rgba(255,255,255,.06); }
    .deviceHeader {
      display:flex;
      justify-content:space-between;
      align-items:flex-start;
      gap:10px;
      cursor:pointer;
    }
    .deviceChevron {
      width:28px;
      height:28px;
      flex:0 0 28px;
      display:inline-flex;
      align-items:center;
      justify-content:center;
      border-radius:50%;
      background:rgba(255,255,255,.07);
      border:1px solid rgba(255,255,255,.11);
      color:var(--muted);
      font-weight:900;
      font-size:18px;
    }
    .client.open .deviceChevron { color:var(--text); }
    .deviceActions { display:none; margin-top:10px; }
    .client.open .deviceActions { display:grid; }
    .detailGrid { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    .detail { background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.06); border-radius:13px; padding:10px; }
    details.dropdown { margin-bottom:12px; }
    details.dropdown summary {
      list-style:none;
      cursor:pointer;
      user-select:none;
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:12px;
      font-size:17px;
      font-weight:850;
      margin-bottom:0;
    }
    details.dropdown summary::-webkit-details-marker { display:none; }
    details.dropdown summary:after {
      content:"+";
      width:28px;
      height:28px;
      flex:0 0 28px;
      display:inline-flex;
      align-items:center;
      justify-content:center;
      border-radius:50%;
      color:var(--text);
      background:rgba(255,255,255,.07);
      border:1px solid rgba(255,255,255,.11);
      font-size:19px;
      font-weight:800;
      line-height:1;
    }
    details.dropdown[open] summary:after {
      content:"−";
      color:var(--muted);
      background:rgba(255,255,255,.04);
    }
    details.dropdown[open] summary { margin-bottom:12px; }
    .qr { width:150px; height:150px; background:#fff; border-radius:12px; padding:8px; }
    .footer { color:var(--muted); font-size:12px; text-align:center; margin:15px 0; }
    .settingsRow { display:grid; grid-template-columns:1fr 1fr; gap:9px; }
    .notice {
      background:rgba(79,140,255,.12); border:1px solid color-mix(in srgb, var(--primary) 35%, transparent);
      color:var(--text); border-radius:16px; padding:12px; margin-bottom:12px;
    }
    .profileNote {
      margin-top:10px; padding:10px; border-radius:14px;
      background:rgba(255,255,255,.045); border:1px solid rgba(255,255,255,.07);
      color:var(--muted); font-size:13px; line-height:1.35;
    }
    .miniBtn {
      min-height:34px; padding:8px 10px; font-size:12px; border-radius:11px;
      margin-top:8px;
    }
    .clientTop {
      display:flex; justify-content:space-between; gap:10px; align-items:flex-start;
    }
    .clientName { font-weight:900; color:var(--text); }
    .clientMeta { color:var(--muted); font-size:12px; line-height:1.35; margin-top:2px; }
    .orderBox {
      display:grid; grid-template-columns:1fr; gap:8px;
      background:rgba(255,255,255,.04); padding:10px; border-radius:14px;
      border:1px solid rgba(255,255,255,.07);
    }
    .row2 { display:grid; grid-template-columns:1fr auto; gap:8px; align-items:center; }
    .statusLine { color:var(--muted); font-size:12px; margin-top:8px; line-height:1.35; }

    .modal {
      position:fixed; inset:0; background:rgba(0,0,0,.72); display:none;
      align-items:flex-end; justify-content:center; z-index:20; padding:12px;
    }
    .modal.show { display:flex; }
    .sheet {
      width:100%; max-width:900px; background:linear-gradient(180deg,var(--card2),var(--card));
      border:1px solid var(--line); border-radius:24px; padding:15px;
      box-shadow:0 18px 46px rgba(0,0,0,.45);
    }
    .sheetTop { display:flex; justify-content:space-between; align-items:center; gap:12px; }
    @media (max-width:520px) {
      body { padding:10px; }
      .rebootRow { grid-template-columns:1fr !important; }
      h1 { font-size:26px; }
      .big { font-size:21px; }
      .value { font-size:16px; }
      .grid3 { grid-template-columns:1fr 1fr; }
      .graphBox { height:255px; }
      .detailGrid { grid-template-columns:1fr; }
      .settingsRow { grid-template-columns:1fr; }
    }
  </style>
</head>
<body>
<div class="app">
  <div class="top">
    <div><h1>ProtonPi</h1><div class="sub">VPN router dashboard</div></div>
    <div id="statePill" class="pill">Checking</div>
  </div>

  <div id="loginWarning" class="warning hidden">You are not signed in. Status is visible, but controls are disabled. <a href="/login" style="color:white;font-weight:900;">Login</a></div>

  <div id="vpnWarning" class="warning hidden">VPN is down while hotspot may still be active. The kill switch should block client traffic.</div>
  <div id="tempWarning" class="badwarn hidden">Pi temperature is high.</div>
  <div id="p2pWarning" class="warning hidden">P2P port may be stale. Renew the port.</div>
  <div id="ethWarning" class="badwarn hidden">Ethernet is not the active internet route. Check eth0.</div>

  <div class="card hero">
    <div class="label">Current IP</div>
    <div id="currentIp" class="big">—</div>
    <div id="serverText" class="small">—</div>
  </div>

  <div class="card">
    <h2>VPN control</h2>
    <div id="profileButtons" class="profiles"></div>
    <div id="profileNote" class="profileNote">—</div>
  </div>

  <div class="card">
    <div class="grid">
      <div class="stat clickable" onclick="openProfileDetails()"><div class="label">Profile</div><div id="profile" class="value">—</div></div>
      <div class="stat"><div class="label">VPN</div><div id="vpn" class="value">—</div></div>
      <div class="stat"><div class="label">Profile uptime</div><div id="profileUptime" class="value">—</div></div>
      <div class="stat p2pOnly hidden"><div class="label">P2P port</div><div id="p2pPort" class="value">—</div></div>
      <div class="stat p2pOnly hidden"><div class="label">Port age</div><div id="portAge" class="value">—</div></div>
      <div class="stat"><div class="label">Live down</div><div id="downSpeed" class="value">—</div></div>
      <div class="stat"><div class="label">Live up</div><div id="upSpeed" class="value">—</div></div>
    </div>
  </div>

  <div class="card">
    <h2>Traffic totals</h2>
    <div class="grid">
      <div class="stat"><div class="label">Current VPN downloaded</div><div id="vpnDownTotal" class="value">—</div></div>
      <div class="stat"><div class="label">Current VPN uploaded</div><div id="vpnUpTotal" class="value">—</div></div>
      <div class="stat"><div class="label">Since boot downloaded</div><div id="bootDownTotal" class="value">—</div></div>
      <div class="stat"><div class="label">Since boot uploaded</div><div id="bootUpTotal" class="value">—</div></div>
      <div class="stat"><div class="label">Monthly downloaded</div><div id="monthDownTotal" class="value">—</div></div>
      <div class="stat"><div class="label">Monthly uploaded</div><div id="monthUpTotal" class="value">—</div></div>
    </div>
  </div>

  <div class="card">
    <h2>Live traffic</h2>
    <div class="graphBox"><canvas id="trafficGraph" width="900" height="340"></canvas></div>
    <div class="legend">
      <span><i class="dot rx"></i>Download</span>
      <span><i class="dot tx"></i>Upload</span>
      <span>Tap/drag</span>
    </div>
  </div>

  <div class="card">
    <h2>Machine</h2>
    <div class="grid3">
      <div class="stat clickable" onclick="openMetric('cpu')"><div class="label">CPU</div><div id="cpu" class="value">—</div></div>
      <div class="stat clickable" onclick="openMetric('temp')"><div class="label">Temp</div><div id="temp" class="value">—</div></div>
      <div class="stat clickable" onclick="openMetric('mem')"><div class="label">Memory</div><div id="mem" class="value">—</div></div>
      <div class="stat"><div class="label">Disk</div><div id="disk" class="value">—</div></div>
      <div class="stat"><div class="label">Uptime</div><div id="uptime" class="value">—</div></div>
      <div class="stat clickable" onclick="openMetric('clients')"><div class="label">Clients</div><div id="clients" class="value">—</div></div>
    </div>
  </div>

  <div class="card">
    <h2>Network health</h2>
    <div class="grid">
      <div class="stat"><div class="label">Internet latency</div><div id="pingInternet" class="value">—</div></div>
      <div class="stat"><div class="label">Proton gateway</div><div id="pingProton" class="value">—</div></div>
      <div class="stat"><div class="label">DNS test</div><div id="dnsStatus" class="value">—</div></div>
      <div class="stat"><div class="label">Client internet</div><div id="pauseStatus" class="value">—</div></div>
    </div>
  </div>

  <div class="card">
    <h2>Tools</h2>
    <div class="profiles">
      <button class="ghost" onclick="doAction('restart_hotspot')">Restart Hotspot</button>
      <button class="ghost" onclick="doAction('renew_p2p')">Renew P2P Port</button>
      <button class="ghost" onclick="clearGraphs()">Clear Graphs</button>
      <button class="ghost" onclick="runDnsTest()">DNS Test</button>
      <button class="ghost" onclick="runSpeed()">Speed Test</button>
      <button id="pauseBtn" class="ghost" onclick="togglePause()">Pause Internet</button>
    </div>

    <div id="speedResult" class="small" style="margin-top:10px;">Speed test is manual to keep Pi load low.</div>
  </div>

  <details class="card dropdown">
    <summary>Connected devices</summary>
    <div id="clientList" class="list small">—</div>
  </details>

  <details class="card dropdown">
    <summary>Wi-Fi QR code</summary>
    <div class="small">Scan to join Pi-Proton-VPN.</div><br>
    <img class="qr" src="/wifi-qr.png">
  </details>


  <details class="card dropdown">
    <summary>Settings</summary>

    <h2>Access</h2>
    <div class="settingsRow">
      <button class="ghost" onclick="changePassword()">Change Password</button>
      <a class="btn ghost" href="/logout">Logout</a>
      <a class="btn ghost" href="/protonpi.crt">Download HTTPS Cert</a>
    </div>

    <br>
    <h2>Profiles</h2>
    <div class="label">Startup profile</div>
    <select id="startupProfile" onchange="setStartupProfile(this.value)">
      <option value="gaming">Gaming on boot</option>
      <option value="p2p">P2P on boot</option>
      <option value="streaming">Streaming on boot</option>
      <option value="maxsec">Max Security on boot</option>
      <option value="off">VPN off on boot</option>
    </select>

    <br><br>
    <div class="label">Fallback profile if active VPN fails</div>
    <select id="fallbackProfile" onchange="saveSettings()">
      <option value="gaming">Fallback to Gaming</option>
      <option value="p2p">Fallback to P2P</option>
      <option value="streaming">Fallback to Streaming</option>
      <option value="maxsec">Fallback to Max Security</option>
      <option value="off">No fallback</option>
    </select>

    <br><br>
    <div class="label">Favorite profile order</div>
    <div class="orderBox">
      <div class="row2">
        <select id="order1" onchange="saveSettings()">
          <option value="gaming">Gaming</option><option value="p2p">P2P</option>
          <option value="streaming">Streaming</option><option value="maxsec">Max Security</option><option value="off">Off</option>
        </select>
        <span class="small">1st</span>
      </div>
      <div class="row2">
        <select id="order2" onchange="saveSettings()">
          <option value="gaming">Gaming</option><option value="p2p">P2P</option>
          <option value="streaming">Streaming</option><option value="maxsec">Max Security</option><option value="off">Off</option>
        </select>
        <span class="small">2nd</span>
      </div>
      <div class="row2">
        <select id="order3" onchange="saveSettings()">
          <option value="gaming">Gaming</option><option value="p2p">P2P</option>
          <option value="streaming">Streaming</option><option value="maxsec">Max Security</option><option value="off">Off</option>
        </select>
        <span class="small">3rd</span>
      </div>
      <div class="row2">
        <select id="order4" onchange="saveSettings()">
          <option value="gaming">Gaming</option><option value="p2p">P2P</option>
          <option value="streaming">Streaming</option><option value="maxsec">Max Security</option><option value="off">Off</option>
        </select>
        <span class="small">4th</span>
      </div>
    </div>

    <br>
    <h2>Scheduled reboot</h2>
    <div class="settingsRow rebootRow">
      <select id="rebootDay" onchange="saveSettings()">
        <option value="off">Off</option>
        <option value="daily">Every day</option>
        <option value="sun">Sunday</option>
        <option value="mon">Monday</option>
        <option value="tue">Tuesday</option>
        <option value="wed">Wednesday</option>
        <option value="thu">Thursday</option>
        <option value="fri">Friday</option>
        <option value="sat">Saturday</option>
      </select>
      <input id="rebootTime" type="time" value="04:00" onchange="saveSettings()">
    </div>

    <br>
    <h2>Appearance</h2>
    <div class="label">Theme</div>
    <select id="theme" onchange="setTheme(this.value)">
      <option value="blue">Blue</option>
      <option value="green">Green</option>
      <option value="purple">Purple</option>
      <option value="red">Red</option>
    </select>

    <br><br>
    <h2>Backup / restore</h2>
    <div class="settingsRow">
      <a class="btn ghost" href="/settings.json">Export Settings</a>
      <button class="ghost" onclick="importSettingsPrompt()">Import Settings</button>
      <button class="ghost" onclick="createFullBackup()">Create Full Backup</button>
      <a class="btn ghost" href="/backup.tar.gz">Download New Backup</a>
    </div>

    <br>
    <div class="label">Restore full backup</div>
    <div class="settingsRow">
      <select id="backupSelect" onclick="loadBackups()">
        <option value="">Load backup list...</option>
      </select>
      <button class="danger" onclick="restoreSelectedBackup()">Restore Selected Backup</button>
    </div>
    <div class="small" style="margin-top:8px;">Restores VPN profiles, dashboard, services, firewall rules, and settings from the selected backup file.</div>

    <br>
    <h2>Maintenance</h2>
    <div class="settingsRow">
      <a class="btn ghost" href="/diagnostics.txt">Diagnostics</a>
      <button class="danger" onclick="doAction('safe_mode')">Safe Mode</button>
      <button class="danger" onclick="doAction('reboot')">Reboot Pi</button>
    </div>

    <div class="small" style="margin-top:10px;">Administrative settings and maintenance tools.</div>
  </details>


  <div class="footer">Refreshes every 3 seconds</div>
</div>


<div id="profileModal" class="modal" onclick="closeProfileDetails(event)">
  <div class="sheet" onclick="event.stopPropagation()">
    <div class="sheetTop">
      <div>
        <h2>Server profile details</h2>
        <div class="small">Current WireGuard profile configuration</div>
      </div>
      <button class="ghost" onclick="hideProfileDetails()">Close</button>
    </div>
    <br>
    <div id="profileDetails" class="detailGrid">—</div>
  </div>
</div>

<div id="modal" class="modal" onclick="closeMetric(event)">
  <div class="sheet" onclick="event.stopPropagation()">
    <div class="sheetTop">
      <div>
        <h2 id="modalTitle">Metric</h2>
        <div id="modalSub" class="small">Last samples</div>
      </div>
      <button class="ghost" onclick="hideMetric()">Close</button>
    </div>
    <br>
    <div class="graphBox"><canvas id="metricGraph" width="900" height="340"></canvas></div>
  </div>
</div>

<script>
let samples = [];
let last = null;
let hoverIndex = null;
let activeMetric = null;

function $(id){ return document.getElementById(id); }
function setText(id,v){ $(id).innerText = (v === null || v === undefined || v === "") ? "—" : v; }
function fmtBytes(n){
  n = Number(n || 0); const u=["B","KB","MB","GB","TB"]; let i=0;
  while(n>=1024 && i<u.length-1){ n/=1024; i++; }
  return n.toFixed(i?1:0)+" "+u[i];
}
function fmtRate(n){ return fmtBytes(n)+"/s"; }
function cssVar(name){ return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function setTheme(t){
  document.documentElement.setAttribute("data-theme", t);
  localStorage.setItem("theme", t);
  const sel = $("theme");
  if(sel) sel.value = t;
  drawTraffic();
  drawMetric();
}
setTheme(localStorage.getItem("theme") || "blue");

const PROFILE_LABELS = {
  gaming:"Gaming",
  p2p:"P2P",
  streaming:"Streaming",
  maxsec:"Max Security",
  off:"Off"
};
const PROFILE_CLASSES = {
  gaming:"gaming",
  p2p:"p2p",
  streaming:"streaming",
  maxsec:"maxsec",
  off:"off"
};
const PROFILE_NOTES = {
  gaming:"Low-latency profile for gaming and general use. Best when you care about ping.",
  p2p:"P2P profile with Proton port forwarding. Use the displayed port in your torrent/P2P app.",
  streaming:"Streaming-focused profile for video services and stable throughput.",
  maxsec:"Secure/privacy-focused profile. Higher latency is expected.",
  off:"VPN is off. Hotspot traffic should be blocked by the kill switch."
};

function profileOrderFromSettings(settings){
  const fallback = ["gaming","p2p","streaming","maxsec","off"];
  const raw = settings && settings.profile_order ? settings.profile_order : fallback;
  const clean = [];
  raw.forEach(x => { if(fallback.includes(x) && !clean.includes(x)) clean.push(x); });
  fallback.forEach(x => { if(!clean.includes(x)) clean.push(x); });
  return clean;
}

function renderProfileButtons(order){
  $("profileButtons").innerHTML = order.map(p => 
    `<button class="${PROFILE_CLASSES[p]}" onclick="setProfile('${p}')">${PROFILE_LABELS[p]}</button>`
  ).join("");
}

async function setProfile(profile){
  setText("vpn","Switching...");
  const r = await fetch("/api/profile",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({profile})});
  const j = await r.json();
  if(!j.ok) alert(j.error || "Failed");
  samples=[]; last=null;
  setTimeout(load,1800);
}

async function setStartupProfile(profile){
  const r = await fetch("/api/default-profile",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({profile})});
  const j = await r.json();
  if(!j.ok) alert(j.error || "Failed to set startup profile");
}

async function saveSettings(){
  const body = {
    fallback_profile: $("fallbackProfile").value,
    reboot_day: $("rebootDay").value,
    reboot_time: $("rebootTime").value,
    profile_order: [$("order1").value, $("order2").value, $("order3").value, $("order4").value, "off"]
  };
  const r = await fetch("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const j = await r.json();
  if(!j.ok) alert(j.error || "Failed to save settings");
}

async function doAction(action){
  if(action==="reboot" && !confirm("Reboot the Raspberry Pi?")) return;
  const r = await fetch("/api/action",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action})});
  const j = await r.json();
  alert(j.message || j.output || "Done");
  setTimeout(load,1000);
}

async function togglePause(){
  const action = $("pauseStatus").innerText.includes("Paused") ? "resume_internet" : "pause_internet";
  await doAction(action);
}

function clearGraphs(){
  samples=[]; last=null; hoverIndex=null;
  drawTraffic(); drawMetric();
}


async function runSpeed(){
  setText("speedResult","Running quick test...");
  const r = await fetch("/api/speedtest",{method:"POST"});
  const j = await r.json();
  setText("speedResult", j.ok ? j.result : (j.error || "Speed test failed"));
}

async function runDnsTest(){
  setText("dnsStatus","Testing...");
  const r = await fetch("/api/dns-test",{method:"POST"});
  const j = await r.json();
  setText("dnsStatus", j.ok ? "OK" : "Fail");
  alert(j.message || JSON.stringify(j));
}

async function changePassword(){
  const oldPassword = prompt("Current dashboard password:");
  if(!oldPassword) return;
  const newPassword = prompt("New dashboard password, minimum 8 characters:");
  if(!newPassword) return;
  if(newPassword.length < 8){
    alert("Password must be at least 8 characters.");
    return;
  }

  const r = await fetch("/api/change-password", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({old_password: oldPassword, new_password: newPassword})
  });
  const j = await r.json();
  alert(j.ok ? "Password changed" : (j.error || "Failed"));
}

function fmtBackupTime(ts){
  try { return new Date(ts * 1000).toLocaleString(); }
  catch(e) { return ""; }
}
function fmtFileSize(n){
  n = Number(n || 0);
  const u = ["B","KB","MB","GB"];
  let i = 0;
  while(n >= 1024 && i < u.length - 1){ n /= 1024; i++; }
  return n.toFixed(i ? 1 : 0) + " " + u[i];
}

async function loadBackups(){
  const sel = $("backupSelect");
  if(!sel) return;
  const r = await fetch("/api/backups?ts=" + Date.now());
  const j = await r.json();
  if(!j.ok){
    sel.innerHTML = `<option value="">Could not load backups</option>`;
    return;
  }
  if(!j.backups.length){
    sel.innerHTML = `<option value="">No backups found</option>`;
    return;
  }
  sel.innerHTML = j.backups.map(b =>
    `<option value="${b.name}">${b.name} · ${fmtFileSize(b.size)} · ${fmtBackupTime(b.mtime)}</option>`
  ).join("");
}

async function createFullBackup(){
  const r = await fetch("/api/create-backup", {method:"POST"});
  const j = await r.json();
  alert(j.ok ? ("Created backup: " + j.name) : (j.error || "Backup failed"));
  loadBackups();
}

async function restoreSelectedBackup(){
  const sel = $("backupSelect");
  const name = sel ? sel.value : "";
  if(!name){
    alert("Select a backup first.");
    return;
  }
  if(!confirm("Restore this full backup? This will overwrite current dashboard/profile/settings files.\\n\\n" + name)){
    return;
  }
  const r = await fetch("/api/restore-backup", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name})
  });
  const j = await r.json();
  alert(j.ok ? "Backup restored. Refresh the dashboard." : (j.error || "Restore failed"));
  setTimeout(()=>location.reload(), 1500);
}

async function importSettingsPrompt(){
  const raw = prompt("Paste exported settings JSON:");
  if(!raw) return;
  try {
    const parsed = JSON.parse(raw);
    const r = await fetch("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(parsed)});
    const j = await r.json();
    if(!j.ok) alert(j.error || "Import failed");
    else { alert("Settings imported"); load(); }
  } catch(e) { alert("Invalid JSON"); }
}


async function checkAuth(){
  try {
    const r = await fetch("/api/auth-status?ts="+Date.now());
    const a = await r.json();
    const authed = !!a.authenticated;
    $("loginWarning").classList.toggle("hidden", authed);
    document.querySelectorAll("button, select, input").forEach(el => {
      if(el.closest("#modal") || el.id === "theme") return;
      if(el.closest(".profiles") || el.closest(".settingsRow") || el.id.includes("Profile") || el.id.includes("reboot") || el.id.includes("order") || el.id === "fallbackProfile") {
        el.disabled = !authed;
        el.style.opacity = authed ? "1" : ".55";
      }
    });
    return authed;
  } catch(e) {
    return false;
  }
}

async function load(){
  checkAuth();
  if($("backupSelect")) loadBackups();
  let s;
  try {
    const r = await fetch("/api/status?ts="+Date.now());
    s = await r.json();
  } catch(e) {
    setText("vpn","API offline");
    return;
  }

  setText("currentIp", s.current_ip);
  setText("serverText", s.server || s.route);
  setText("profile", s.profile);
  setText("vpn", s.vpn_up ? "Connected" : "Disconnected");
  if(s.default_profile) $("startupProfile").value = s.default_profile;
  if(s.settings){
    $("fallbackProfile").value = s.settings.fallback_profile || "gaming";
    $("rebootDay").value = s.settings.reboot_day || "off";
    $("rebootTime").value = s.settings.reboot_time || "04:00";
    const order = profileOrderFromSettings(s.settings);
    ["order1","order2","order3","order4"].forEach((id,i)=>{ if($(id)) $(id).value = order[i]; });
    renderProfileButtons(order);
  } else {
    renderProfileButtons(["gaming","p2p","streaming","maxsec","off"]);
  }

  const pill=$("statePill");
  pill.innerText = s.vpn_up ? "Protected" : "Offline";
  pill.style.color = s.vpn_up ? "var(--good)" : "var(--bad)";

  $("vpnWarning").classList.toggle("hidden", s.vpn_up || s.profile === "off");
  $("tempWarning").classList.toggle("hidden", !(s.machine.cpu_temp_c && s.machine.cpu_temp_c >= 75));
  $("tempWarning").innerText = s.machine.cpu_temp_c ? `Pi temperature is high: ${s.machine.cpu_temp_c.toFixed(1)}°C` : "Pi temperature is high.";
  $("p2pWarning").classList.toggle("hidden", !s.port_age_warning);
  $("ethWarning").classList.toggle("hidden", !s.eth_warning);
  if(s.eth_warning) $("ethWarning").innerText = s.eth_warning;
  setText("profileUptime", s.profile_uptime);
  setText("profileNote", PROFILE_NOTES[s.profile] || "—");

  const p2p = s.profile === "p2p" && s.vpn_up;
  document.querySelectorAll(".p2pOnly").forEach(x=>x.classList.toggle("hidden",!p2p));
  setText("p2pPort", s.tcp_port);
  setText("portAge", s.port_age_warning ? (s.port_age + " ⚠") : s.port_age);

  setText("pingInternet", s.latency.internet_ms === null ? "—" : s.latency.internet_ms + " ms");
  setText("pingProton", s.latency.proton_ms === null ? "—" : s.latency.proton_ms + " ms");
  setText("pauseStatus", s.internet_paused ? "Paused" : "Allowed");
  $("pauseBtn").innerText = s.internet_paused ? "Resume Internet" : "Pause Internet";

  let down=0, up=0;
  if(last && s.interfaces && s.interfaces.wg0){
    const dt=Math.max(s.time-last.time,1);
    down=Math.max((s.interfaces.wg0.rx-last.interfaces.wg0.rx)/dt,0);
    up=Math.max((s.interfaces.wg0.tx-last.interfaces.wg0.tx)/dt,0);
  }
  last=s;

  setText("downSpeed", fmtRate(down));
  setText("upSpeed", fmtRate(up));
  setText("vpnDownTotal", fmtBytes(s.totals.current_vpn.rx));
  setText("vpnUpTotal", fmtBytes(s.totals.current_vpn.tx));
  setText("bootDownTotal", fmtBytes(s.totals.since_boot.rx));
  setText("bootUpTotal", fmtBytes(s.totals.since_boot.tx));
  setText("monthDownTotal", fmtBytes(s.totals.monthly.rx));
  setText("monthUpTotal", fmtBytes(s.totals.monthly.tx));

  samples.push({
    t:new Date().toLocaleTimeString(),
    down, up,
    cpu:s.machine.cpu_percent,
    temp:s.machine.cpu_temp_c,
    mem:s.machine.mem_used_percent,
    clients:s.hotspot_clients
  });
  if(samples.length>90) samples.shift();

  drawTraffic();
  drawMetric();

  setText("cpu", s.machine.cpu_percent + "%");
  setText("temp", s.machine.cpu_temp_c === null ? "—" : s.machine.cpu_temp_c.toFixed(1)+"°C");
  setText("mem", s.machine.mem_used_percent + "%");
  setText("disk", s.machine.disk_used_percent + "%");
  setText("uptime", s.machine.uptime);
  setText("clients", s.hotspot_clients);

  renderProfileDetails(s.profile_details || {});
  renderClients(s.clients || []);
}

function renderProfileDetails(d){
  const items = [
    ["Server", d.server],
    ["Endpoint", d.endpoint],
    ["NetShield", d.NetShield],
    ["Moderate NAT", d["Moderate NAT"]],
    ["Port forwarding", d["NAT-PMP (Port Forwarding)"]],
    ["VPN Accelerator", d["VPN Accelerator"]],
    ["Secure Core / Bouncing", d.Bouncing]
  ].filter(x => x[1] !== undefined && x[1] !== null && x[1] !== "");
  $("profileDetails").innerHTML = items.length ? items.map(([k,v]) =>
    `<div class="detail"><div class="label">${k}</div><div class="value">${v}</div></div>`
  ).join("") : "No profile details available";
}

function toggleDeviceCard(el){
  el.classList.toggle("open");
  const icon = el.querySelector(".deviceChevron");
  if(icon) icon.innerText = el.classList.contains("open") ? "−" : "+";
}

function renderClients(clients){
  if(clients.length){
    $("clientList").innerHTML = clients.map((c, idx)=>{
      const name = c.name || ("Device " + c.ip);
      const policy = c.policy ? `<br>Policy: ${c.policy}` : "";
      return `<div class="client" id="client-${idx}">
        <div class="deviceHeader" onclick="toggleDeviceCard(document.getElementById('client-${idx}'))">
          <div>
            <div class="clientName">${name}</div>
            <div class="clientMeta">${c.ip} · ${c.mac} · ${c.state}<br>Approx RX/TX: ${fmtBytes(c.rx)} / ${fmtBytes(c.tx)}${policy}</div>
          </div>
          <div class="deviceChevron">+</div>
        </div>
        <div class="profiles deviceActions">
          <button class="ghost miniBtn" onclick="renameClient('${c.mac}','${c.ip}')">Rename</button>
          <button class="ghost miniBtn" onclick="deviceControl('block','${c.ip}')">Block</button>
          <button class="ghost miniBtn" onclick="deviceControl('unblock','${c.ip}')">Unblock</button>
          <button class="ghost miniBtn" onclick="deviceControl('prioritize','${c.ip}')">Prioritize</button>
          <button class="ghost miniBtn" onclick="limitDevice('${c.ip}')">Limit</button>
          <button class="ghost miniBtn" onclick="deviceControl('clear_limit','${c.ip}')">Clear Policy</button>
        </div>
      </div>`;
    }).join("");
  } else {
    $("clientList").innerText = "No clients detected";
  }
}

async function renameClient(mac, ip){
  const name = prompt("Device name:", "");
  if(!name) return;
  const r = await fetch("/api/client-name", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({mac, ip, name})
  });
  const j = await r.json();
  if(!j.ok) alert(j.error || "Failed to rename device");
  load();
}

async function deviceControl(action, ip){
  const r = await fetch("/api/device-control", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({action, ip})
  });
  const j = await r.json();
  alert(j.ok ? (j.message || "Done") : (j.error || "Failed"));
  load();
}

async function limitDevice(ip){
  const mbps = prompt("Download limit Mbps for " + ip + " (example: 10). Leave blank to clear:", "");
  const action = mbps ? "limit" : "clear_limit";
  const r = await fetch("/api/device-control", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({action, ip, mbps})
  });
  const j = await r.json();
  alert(j.ok ? (j.message || "Done") : (j.error || "Failed"));
  load();
}

function drawLineGraph(canvasId, data, series, opts={}){
  const c=$(canvasId), ctx=c.getContext("2d"), W=c.width, H=c.height;
  ctx.clearRect(0,0,W,H);
  const L=64,R=18,T=24,B=42,w=W-L-R,h=H-T-B;
  ctx.fillStyle="rgba(0,0,0,.14)";
  ctx.fillRect(0,0,W,H);

  const vals = data.flatMap(p => series.map(s => Number(p[s.key] || 0)));
  let maxVal;
  if(opts.maxOverride) maxVal = opts.maxOverride;
  else if(opts.percent) maxVal = 100;
  else maxVal = niceMax(Math.max(opts.minMax || 1, ...vals));

  ctx.font="12px Arial";
  ctx.strokeStyle="rgba(255,255,255,.09)";
  ctx.fillStyle="rgba(244,247,251,.68)";
  for(let i=0;i<=4;i++){
    const y=T+h*i/4;
    ctx.beginPath(); ctx.moveTo(L,y); ctx.lineTo(W-R,y); ctx.stroke();
    const val=maxVal*(1-i/4);
    ctx.fillText(opts.format ? opts.format(val) : fmtRate(val).replace("/s",""),8,y+4);
  }

  if(data.length<2) return;

  const x=i=>L+(i/(data.length-1))*w;
  const y=v=>T+h-(Number(v || 0)/maxVal)*h;

  series.forEach(sr=>{
    ctx.beginPath();
    data.forEach((p,i)=>{ if(i===0) ctx.moveTo(x(i),y(p[sr.key])); else ctx.lineTo(x(i),y(p[sr.key])); });
    ctx.strokeStyle=sr.color;
    ctx.lineWidth=3;
    ctx.stroke();

    ctx.lineTo(x(data.length-1), H-B);
    ctx.lineTo(x(0), H-B);
    ctx.closePath();
    const grad=ctx.createLinearGradient(0,T,0,H-B);
    grad.addColorStop(0, sr.fill || "rgba(255,255,255,.10)");
    grad.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle=grad;
    ctx.fill();
  });

  if(hoverIndex !== null && data[hoverIndex]){
    const xx=x(hoverIndex), p=data[hoverIndex];
    ctx.strokeStyle="rgba(255,255,255,.40)";
    ctx.beginPath(); ctx.moveTo(xx,T); ctx.lineTo(xx,H-B); ctx.stroke();

    ctx.fillStyle="rgba(0,0,0,.75)";
    ctx.fillRect(Math.min(xx+8,W-235),T+8,220,22+series.length*18);
    ctx.fillStyle="#fff";
    ctx.fillText(p.t || "",Math.min(xx+18,W-225),T+28);
    series.forEach((sr,i)=>{
      const txt = `${sr.label}: ${opts.format ? opts.format(p[sr.key]) : fmtRate(p[sr.key])}`;
      ctx.fillText(txt,Math.min(xx+18,W-225),T+48+i*18);
    });
  }

  ctx.fillStyle="rgba(244,247,251,.72)";
  ctx.fillText(data[0].t,L,H-12);
  ctx.fillText(data[data.length-1].t,W-96,H-12);
}

function niceMax(v){
  if(v<=1024) return 1024;
  const p=Math.pow(10,Math.floor(Math.log10(v)));
  return Math.ceil(v/p)*p;
}

function drawTraffic(){
  drawLineGraph("trafficGraph", samples, [
    {key:"down",label:"Down",color:cssVar("--primary"),fill:"rgba(79,140,255,.18)"},
    {key:"up",label:"Up",color:cssVar("--good"),fill:"rgba(53,208,127,.14)"}
  ]);
}

function openProfileDetails(){
  $("profileModal").classList.add("show");
}
function hideProfileDetails(){
  $("profileModal").classList.remove("show");
}
function closeProfileDetails(e){
  if(e.target.id==="profileModal") hideProfileDetails();
}

function openMetric(metric){
  activeMetric = metric;
  $("modal").classList.add("show");
  const titles = {cpu:"CPU usage", temp:"CPU temperature", mem:"Memory usage", clients:"Connected clients"};
  $("modalTitle").innerText = titles[metric] || "Metric";
  drawMetric();
}

function hideMetric(){ $("modal").classList.remove("show"); activeMetric=null; }
function closeMetric(e){ if(e.target.id==="modal") hideMetric(); }

function drawMetric(){
  if(!activeMetric) return;
  const format = activeMetric==="temp" ? (v)=>Number(v||0).toFixed(1)+"°C" :
                 activeMetric==="clients" ? (v)=>String(Math.round(v||0)) :
                 (v)=>Number(v||0).toFixed(1)+"%";
  drawLineGraph("metricGraph", samples, [
    {key:activeMetric,label:$("modalTitle").innerText,color:cssVar("--primary"),fill:"rgba(79,140,255,.18)"}
  ], {
    format,
    percent: activeMetric==="cpu" || activeMetric==="mem",
    maxOverride: activeMetric==="temp" ? 100 : null,
    minMax: activeMetric==="clients" ? 3 : 1
  });
}

$("trafficGraph").addEventListener("pointermove", e=>{
  const rect=$("trafficGraph").getBoundingClientRect();
  const ratio=(e.clientX-rect.left)/rect.width;
  hoverIndex=Math.max(0,Math.min(samples.length-1,Math.round(ratio*(samples.length-1))));
  drawTraffic(); drawMetric();
});
$("trafficGraph").addEventListener("pointerleave",()=>{hoverIndex=null;drawTraffic();drawMetric();});

load();
setInterval(load,3000);
</script>
</body>
</html>
"""

def run(cmd, timeout=6):
    try:
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=timeout).strip()
    except subprocess.CalledProcessError as e:
        return e.output.strip()
    except Exception:
        return ""

def read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except:
        return None

def ensure_dirs():
    os.makedirs(PERSIST_DIR, exist_ok=True)

def load_settings():
    try:
        return json.load(open(SETTINGS_FILE))
    except Exception:
        return {
            "fallback_profile": "gaming",
            "reboot_day": "off",
            "reboot_time": "04:00",
            "profile_order": ["gaming", "p2p", "streaming", "maxsec", "off"]
        }

def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
        return True
    except Exception:
        return False

def apply_reboot_cron(settings):
    day = settings.get("reboot_day", "off")
    t = settings.get("reboot_time", "04:00")
    if not re.match(r"^\d{2}:\d{2}$", t):
        t = "04:00"
    hour, minute = t.split(":")
    day_map = {"sun":"0","mon":"1","tue":"2","wed":"3","thu":"4","fri":"5","sat":"6"}

    run(["sh","-c","crontab -l 2>/dev/null | grep -v 'ProtonPi scheduled reboot' > /tmp/protonpi-cron || true"],4)

    if day == "off":
        run(["sh","-c","crontab /tmp/protonpi-cron"],4)
        return True

    if day == "daily":
        cron_day = "*"
    elif day in day_map:
        cron_day = day_map[day]
    else:
        return False

    line = f"{int(minute)} {int(hour)} * * {cron_day} /sbin/reboot # ProtonPi scheduled reboot"
    run(["sh","-c",f"echo '{line}' >> /tmp/protonpi-cron && crontab /tmp/protonpi-cron"],4)
    return True

def default_profile():
    val = read("/etc/protonvpn-profiles/default-profile")
    return val if val in ["gaming","p2p","maxsec","streaming","off"] else "gaming"

def set_default_profile(profile):
    if profile not in ["gaming","p2p","maxsec","streaming","off"]:
        return False
    try:
        with open("/etc/protonvpn-profiles/default-profile","w") as f:
            f.write(profile)
        return True
    except:
        return False

def iface_bytes(iface):
    base=f"/sys/class/net/{iface}/statistics"
    rx=read(f"{base}/rx_bytes")
    tx=read(f"{base}/tx_bytes")
    return {"rx": int(rx) if rx and rx.isdigit() else 0, "tx": int(tx) if tx and tx.isdigit() else 0}

def cached_value(key, ttl, func):
    now = time.time()
    item = STATUS_CACHE.get(key)
    if item and now - item.get("time", 0) < ttl:
        return item.get("value")
    value = func()
    STATUS_CACHE[key] = {"time": now, "value": value}
    return value

def current_ip():
    def _get():
        for cmd in [
            ["curl","-4","-s","--max-time","2","https://api.ipify.org"],
            ["curl","-4","-s","--max-time","2","https://ipv4.icanhazip.com"],
            ["curl","-4","-s","--max-time","2","https://ifconfig.me/ip"]
        ]:
            ip=run(cmd,3).strip()
            if re.match(r"^\d{1,3}(\.\d{1,3}){3}$",ip):
                return ip
        return None
    return cached_value("current_ip", 20, _get)

def cpu_percent():
    global LAST_CPU
    try:
        with open("/proc/stat") as f:
            line=f.readline()
        parts=line.split()
        vals=list(map(int,parts[1:]))
        idle=vals[3]+vals[4]
        total=sum(vals)
        if LAST_CPU["total"] is None:
            LAST_CPU={"total":total,"idle":idle}
            return 0
        dt=total-LAST_CPU["total"]
        di=idle-LAST_CPU["idle"]
        LAST_CPU={"total":total,"idle":idle}
        return round(max(0,min(100,(1-di/dt)*100)),1) if dt else 0
    except:
        return 0

def mem_percent():
    try:
        data={}
        for line in open("/proc/meminfo"):
            data[line.split(":")[0]]=int(line.split(":")[1].strip().split()[0])
        return round((1-data.get("MemAvailable",0)/data.get("MemTotal",1))*100,1)
    except:
        return 0

def disk_percent():
    try:
        st=os.statvfs("/")
        total=st.f_blocks*st.f_frsize
        free=st.f_bavail*st.f_frsize
        return round((1-free/total)*100,1)
    except:
        return 0

def cpu_temp():
    val=read("/sys/class/thermal/thermal_zone0/temp")
    return int(val)/1000 if val and val.isdigit() else None

def uptime():
    try:
        sec=int(float(read("/proc/uptime").split()[0]))
        d,rem=divmod(sec,86400); h,rem=divmod(rem,3600); m,_=divmod(rem,60)
        return f"{d}d {h}h" if d else (f"{h}h {m}m" if h else f"{m}m")
    except:
        return "—"

def client_names():
    try:
        return json.load(open(CLIENT_NAMES_FILE))
    except Exception:
        return {}

def save_client_name(mac, ip, name):
    data = client_names()
    if mac and mac != "unknown":
        data[mac.lower()] = name
    if ip:
        data[ip] = name
    try:
        with open(CLIENT_NAMES_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False

def profile_started_at():
    try:
        return os.path.getmtime("/run/vpn-profile-current")
    except Exception:
        return None

def profile_uptime():
    t = profile_started_at()
    if not t:
        return "—"
    sec = int(time.time() - t)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"

def eth_warning():
    def _get():
        devs = run(["nmcli", "-t", "-f", "DEVICE,STATE,CONNECTION", "device", "status"], 3)
        route = run(["sh", "-c", "ip route | grep default | head -n1"], 2)

        if "eth0:connected" not in devs:
            return "Ethernet eth0 is not connected. The Pi should use Ethernet as its internet source."

        if "dev eth0" not in route:
            return f"Default route is not using eth0: {route}"

        if "dev wlan0" in route:
            return "Warning: a Wi-Fi default route exists. Keep wlan0 as hotspot only."

        return None
    return cached_value("eth_warning", 10, _get)

def client_policies():
    try:
        return json.load(open(CLIENT_POLICIES_FILE))
    except Exception:
        return {}

def save_client_policy(ip, policy):
    data = client_policies()
    if policy:
        data[ip] = policy
    else:
        data.pop(ip, None)
    try:
        with open(CLIENT_POLICIES_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False

def clients():
    aliases = client_names()
    policies = client_policies()
    lease_names={}
    for p in ["/var/lib/NetworkManager/dnsmasq-Hotspot.leases","/var/lib/misc/dnsmasq.leases","/run/NetworkManager/dnsmasq-Hotspot.leases"]:
        if os.path.exists(p):
            try:
                for line in open(p):
                    parts=line.split()
                    if len(parts)>=4:
                        name = parts[3] if parts[3]!="*" else f"Device {parts[2]}"
                        lease_names[parts[2]]={"mac":parts[1],"name":name}
            except:
                pass
    rows=[]
    neigh=run(["ip","-4","neigh","show","dev","wlan0"],3)
    for line in neigh.splitlines():
        parts=line.split()
        if not parts: continue
        ip=parts[0]
        mac="unknown"
        state=parts[-1] if parts else "unknown"
        if "lladdr" in parts:
            try: mac=parts[parts.index("lladdr")+1]
            except: pass
        name=aliases.get(mac.lower()) or aliases.get(ip) or lease_names.get(ip,{}).get("name",f"Device {ip}")
        if ip.startswith("10.42.") or mac!="unknown":
            rows.append({"ip":ip,"mac":mac,"name":name,"state":state})
    known={r["ip"] for r in rows}
    for ip,d in lease_names.items():
        if ip not in known:
            rows.append({"ip":ip,"mac":d["mac"],"name":d["name"],"state":"leased"})
    rows = client_traffic(rows)
    for r in rows:
        r["policy"] = policies.get(r["ip"])
    return rows

def setup_client_counter_chains(ips):
    global CLIENT_COUNTER_LAST_SETUP
    now = time.time()
    if now - CLIENT_COUNTER_LAST_SETUP < 15:
        return
    CLIENT_COUNTER_LAST_SETUP = now

    run(["sh","-c","iptables -N PP_CLIENT_TX 2>/dev/null || true"],3)
    run(["sh","-c","iptables -N PP_CLIENT_RX 2>/dev/null || true"],3)
    run(["sh","-c","iptables -C FORWARD -j PP_CLIENT_TX 2>/dev/null || iptables -I FORWARD 1 -j PP_CLIENT_TX"],3)
    run(["sh","-c","iptables -C FORWARD -j PP_CLIENT_RX 2>/dev/null || iptables -I FORWARD 1 -j PP_CLIENT_RX"],3)

    for ip in ips:
        if re.match(r"^10\.42\.\d{1,3}\.\d{1,3}$", ip):
            run(["sh","-c",f"iptables -C PP_CLIENT_TX -s {ip} -j RETURN 2>/dev/null || iptables -A PP_CLIENT_TX -s {ip} -j RETURN"],3)
            run(["sh","-c",f"iptables -C PP_CLIENT_RX -d {ip} -j RETURN 2>/dev/null || iptables -A PP_CLIENT_RX -d {ip} -j RETURN"],3)

def parse_chain_bytes(chain, mode):
    out=run(["iptables","-vnx","-L",chain],4)
    data={}
    for line in out.splitlines():
        parts=line.split()
        if len(parts) < 8: continue
        if parts[2] != "RETURN": continue
        try:
            b=int(parts[1])
        except:
            continue
        src=parts[7] if len(parts)>7 else ""
        dst=parts[8] if len(parts)>8 else ""
        ip = src if mode=="tx" else dst
        if ip.startswith("10.42."):
            data[ip]=b
    return data

def client_traffic(rows):
    ips=[r["ip"] for r in rows if r["ip"].startswith("10.42.")]
    setup_client_counter_chains(ips)
    tx=parse_chain_bytes("PP_CLIENT_TX","tx")
    rx=parse_chain_bytes("PP_CLIENT_RX","rx")
    for r in rows:
        r["tx"]=tx.get(r["ip"],0)
        r["rx"]=rx.get(r["ip"],0)
    return rows

def server_detail():
    conf=read("/etc/wireguard/wg0.conf") or ""
    name=""; endpoint=""
    for line in conf.splitlines():
        line=line.strip()
        if line.startswith("# "):
            text=line[2:].strip()
            if re.match(r"^[A-Z]{2}-[A-Z]{2}#\d+", text):
                name=text
        elif line.startswith("Endpoint"):
            endpoint=line.split("=",1)[1].strip()
    return f"{name} · {endpoint}" if name else endpoint

def profile_details():
    conf=read("/etc/wireguard/wg0.conf") or ""
    d={}
    allowed={"NetShield","Moderate NAT","NAT-PMP (Port Forwarding)","VPN Accelerator","Bouncing"}
    for line in conf.splitlines():
        line=line.strip()
        if line.startswith("# "):
            text=line[2:].strip()
            if " = " in text:
                k,v=text.split(" = ",1)
                if k.strip() in allowed:
                    d[k.strip()]=v.strip()
            elif re.match(r"^[A-Z]{2}-[A-Z]{2}#\d+", text):
                d["server"]=text
        elif line.startswith("Endpoint"):
            d["endpoint"]=line.split("=",1)[1].strip()
    return d

def port_age_seconds():
    t=read("/run/proton-forwarded-updated")
    if not t: return None
    try:
        updated=time.mktime(time.strptime(t,"%Y-%m-%d %H:%M:%S"))
        return max(0,int(time.time()-updated))
    except:
        return None

def port_age():
    age=port_age_seconds()
    return None if age is None else f"{age}s ago"

def load_json(path, default):
    try:
        return json.load(open(path))
    except:
        return default

def save_json(path, data):
    try:
        if path.startswith("/var/lib"):
            ensure_dirs()
        with open(path,"w") as f:
            json.dump(data,f)
    except:
        pass

def traffic_totals(wg):
    rx=wg.get("rx",0)
    tx=wg.get("tx",0)

    run_state=load_json(RUN_TRAFFIC_STATE,{"last_rx":0,"last_tx":0,"boot_rx":0,"boot_tx":0})
    if rx >= run_state.get("last_rx",0):
        run_state["boot_rx"] += rx-run_state.get("last_rx",0)
    else:
        run_state["boot_rx"] += rx
    if tx >= run_state.get("last_tx",0):
        run_state["boot_tx"] += tx-run_state.get("last_tx",0)
    else:
        run_state["boot_tx"] += tx
    run_state["last_rx"]=rx
    run_state["last_tx"]=tx
    save_json(RUN_TRAFFIC_STATE,run_state)

    month_key=time.strftime("%Y-%m")
    mon=load_json(PERSIST_TRAFFIC_STATE,{"month":month_key,"last_rx":0,"last_tx":0,"rx":0,"tx":0})
    if mon.get("month") != month_key:
        mon={"month":month_key,"last_rx":0,"last_tx":0,"rx":0,"tx":0}

    if rx >= mon.get("last_rx",0):
        mon["rx"] += rx-mon.get("last_rx",0)
    else:
        mon["rx"] += rx
    if tx >= mon.get("last_tx",0):
        mon["tx"] += tx-mon.get("last_tx",0)
    else:
        mon["tx"] += tx
    mon["last_rx"]=rx
    mon["last_tx"]=tx
    save_json(PERSIST_TRAFFIC_STATE,mon)

    return {
        "current_vpn":{"rx":rx,"tx":tx},
        "since_boot":{"rx":run_state.get("boot_rx",0),"tx":run_state.get("boot_tx",0)},
        "monthly":{"rx":mon.get("rx",0),"tx":mon.get("tx",0),"month":month_key}
    }

def ping_ms(host):
    def _get():
        out=run(["ping","-c","1","-W","1",host],2)
        m=re.search(r"time=([0-9.]+)",out)
        return round(float(m.group(1)),1) if m else None
    return cached_value("ping_" + host, 10, _get)

def internet_paused():
    out=run(["sh","-c","iptables -C FORWARD -i wlan0 -m comment --comment PROTONPI_PAUSE -j REJECT 2>/dev/null && echo yes || echo no"],3)
    return out.strip()=="yes"

def pause_internet():
    run(["sh","-c","iptables -C FORWARD -i wlan0 -m comment --comment PROTONPI_PAUSE -j REJECT 2>/dev/null || iptables -I FORWARD 1 -i wlan0 -m comment --comment PROTONPI_PAUSE -j REJECT"],3)

def resume_internet():
    run(["sh","-c","while iptables -C FORWARD -i wlan0 -m comment --comment PROTONPI_PAUSE -j REJECT 2>/dev/null; do iptables -D FORWARD -i wlan0 -m comment --comment PROTONPI_PAUSE -j REJECT; done"],3)

def dns_test():
    try:
        socket.gethostbyname("protonvpn.com")
        ip=current_ip()
        return {"ok": True, "message": f"DNS resolution works. Current IP: {ip or 'unknown'}"}
    except Exception as e:
        return {"ok": False, "message": f"DNS failed: {e}"}


def dashboard_locked():
    return bool(load_settings().get("dashboard_locked", False))

def read_only_mode():
    return bool(load_settings().get("read_only_mode", False))

def blocked_when_locked():
    return dashboard_locked() or read_only_mode()


def health_score():
    try:
        return json.loads(run(["/usr/local/sbin/protonpi-health"], 8))
    except Exception:
        return {"score": 0}

def throttling_status():
    out = run(["vcgencmd", "get_throttled"], 3)
    if not out:
        return "unknown"
    return out


LOGIN_HTML = """
<!doctype html>
<html>
<head>
  <title>ProtonPi Login</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {
      margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
      background:radial-gradient(circle at top,#18223c,#05070d 55%);
      color:#f4f7fb; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
      padding:18px;
    }
    .card {
      width:100%; max-width:380px; background:linear-gradient(180deg,#182236,#111827);
      border:1px solid rgba(255,255,255,.12); border-radius:24px; padding:22px;
      box-shadow:0 18px 46px rgba(0,0,0,.4);
    }
    h1 { margin:0 0 6px; font-size:28px; }
    p { color:#91a0b6; margin:0 0 18px; }
    input {
      width:100%; box-sizing:border-box; border:1px solid rgba(255,255,255,.12);
      background:rgba(255,255,255,.08); color:white; border-radius:16px;
      padding:14px; font-size:16px; margin-bottom:12px;
    }
    label { display:flex; gap:8px; align-items:center; color:#91a0b6; font-size:14px; margin-bottom:14px; }
    label input { width:auto; margin:0; }
    button {
      width:100%; border:0; border-radius:16px; padding:14px; color:white;
      background:linear-gradient(135deg,#4f8cff,#7aa7ff); font-weight:900; font-size:15px;
    }
    .err { color:#ff9aa6; margin-bottom:12px; }
  </style>
</head>
<body>
  <form class="card" method="post">
    <h1>ProtonPi</h1>
    <p>Sign in to control the VPN router.</p>
    {% if error %}<div class="err">{{ error }}</div>{% endif %}
    <input type="password" name="password" placeholder="Dashboard password" autofocus>
    <label><input type="checkbox" name="trust" value="1" checked> Trust this device</label>
    <button type="submit">Sign in</button>
  </form>
</body>
</html>
"""

def load_auth():
    try:
        return json.load(open(AUTH_FILE))
    except Exception:
        return {"trusted_devices": {}}

def save_auth(data):
    try:
        with open(AUTH_FILE, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(AUTH_FILE, 0o600)
        return True
    except Exception:
        return False

def is_trusted_device():
    token = request.cookies.get("protonpi_trust")
    if not token:
        return False
    data = load_auth()
    trusted = data.get("trusted_devices", {})
    item = trusted.get(token)
    if not item:
        return False
    return True

def is_authed():
    return bool(session.get("authed")) or is_trusted_device()

def require_control_auth():
    if not is_authed():
        return jsonify({"ok": False, "error": "Login required"}), 401
    return None



@app.after_request
def add_security_headers(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    if request.scheme == "https":
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        data = load_auth()
        pw_hash = data.get("password_hash")
        if pw_hash and check_password_hash(pw_hash, password):
            session["authed"] = True
            resp = make_response(redirect("/"))
            if request.form.get("trust") == "1":
                token = secrets.token_urlsafe(32)
                data.setdefault("trusted_devices", {})[token] = {
                    "created": int(time.time()),
                    "ip": request.remote_addr,
                    "user_agent": request.headers.get("User-Agent", "")
                }
                save_auth(data)
                resp.set_cookie("protonpi_trust", token, max_age=60*60*24*365, httponly=True, samesite="Lax")
            return resp
        error = "Wrong password"
    return render_template_string(LOGIN_HTML, error=error)

@app.route("/logout")
def logout():
    session.clear()
    resp = make_response(redirect("/login"))
    resp.delete_cookie("protonpi_trust")
    return resp

@app.route("/api/auth-status")
def api_auth_status():
    return jsonify({"authenticated": is_authed(), "trusted": is_trusted_device()})

@app.route("/api/trusted-devices")
def api_trusted_devices():
    auth = require_control_auth()
    if auth: return auth
    data = load_auth()
    return jsonify(data.get("trusted_devices", {}))

@app.route("/api/trusted-devices/revoke", methods=["POST"])
def api_revoke_trusted_device():
    auth = require_control_auth()
    if auth: return auth
    token = request.get_json(force=True).get("token", "")
    data = load_auth()
    data.get("trusted_devices", {}).pop(token, None)
    save_auth(data)
    return jsonify({"ok": True})


@app.route("/")
def index():
    if not is_authed():
        return redirect("/login")
    return render_template_string(HTML)

@app.route("/api/status")
def status():
    auth = require_control_auth()
    if auth: return auth
    wg_raw=run(["wg"],3)
    vpn_up="interface: wg0" in wg_raw
    profile=read("/run/vpn-profile-current") or "unknown"
    age_sec=port_age_seconds()
    cl=clients()
    wg=iface_bytes("wg0")
    temp=cpu_temp()
    settings=load_settings()

    return jsonify({
        "time":time.time(),
        "profile":profile,
        "profile_uptime":profile_uptime(),
        "default_profile":default_profile(),
        "eth_warning":eth_warning(),
        "settings":settings,
        "dashboard_locked": dashboard_locked(),
        "read_only_mode": read_only_mode(),
        "vpn_up":vpn_up,
        "current_ip":current_ip() if vpn_up else None,
        "server":server_detail() if vpn_up else None,
        "profile_details":profile_details() if vpn_up else {},
        "route":run(["sh","-c","ip route | grep default | head -n1"],2),
        "tcp_port":read("/run/proton-forwarded-tcp-port") if profile=="p2p" and vpn_up else None,
        "udp_port":read("/run/proton-forwarded-udp-port") if profile=="p2p" and vpn_up else None,
        "port_age":port_age() if profile=="p2p" and vpn_up else None,
        "port_age_warning": True if (profile=="p2p" and vpn_up and age_sec is not None and age_sec > 70) else False,
        "interfaces":{"eth0":iface_bytes("eth0"),"wlan0":iface_bytes("wlan0"),"wg0":wg},
        "totals":traffic_totals(wg),
        "machine":{"cpu_percent":cpu_percent(),"cpu_temp_c":temp,"mem_used_percent":mem_percent(),"disk_used_percent":disk_percent(),"uptime":uptime()},
        "hotspot_clients":len(cl),
        "clients":cl,
        "internet_paused":internet_paused(),
        "latency":{"internet_ms":ping_ms("1.1.1.1"),"proton_ms":ping_ms("10.2.0.1") if vpn_up else None},
        "health": health_score(),
        "throttling": throttling_status()
    })

@app.route("/api/profile",methods=["POST"])
def profile():
    auth = require_control_auth()
    if auth: return auth
    if blocked_when_locked():
        return jsonify({"ok":False,"error":"Dashboard is locked or read-only"}),403
    p=request.get_json(force=True).get("profile","")
    if p not in ["gaming","p2p","maxsec","streaming","off"]:
        return jsonify({"ok":False,"error":"Invalid profile"}),400
    out=run(["/usr/local/sbin/vpn-profile",p],50)
    return jsonify({"ok":True,"output":out})

@app.route("/api/default-profile",methods=["POST"])
def api_default_profile():
    auth = require_control_auth()
    if auth: return auth
    p=request.get_json(force=True).get("profile","")
    if set_default_profile(p):
        return jsonify({"ok":True,"profile":p})
    return jsonify({"ok":False,"error":"Could not set default profile"}),500

@app.route("/api/settings",methods=["POST"])
def api_settings():
    auth = require_control_auth()
    if auth: return auth
    data=request.get_json(force=True)
    settings=load_settings()
    for k in ["fallback_profile","reboot_day","reboot_time","profile_order"]:
        if k in data:
            settings[k]=data[k]

    if settings.get("fallback_profile") not in ["gaming","p2p","streaming","maxsec","off"]:
        return jsonify({"ok":False,"error":"Invalid fallback profile"}),400

    if settings.get("reboot_day") not in ["off","daily","sun","mon","tue","wed","thu","fri","sat"]:
        return jsonify({"ok":False,"error":"Invalid reboot day"}),400

    if not re.match(r"^\d{2}:\d{2}$", settings.get("reboot_time","04:00")):
        return jsonify({"ok":False,"error":"Invalid reboot time"}),400

    allowed_profiles = ["gaming","p2p","streaming","maxsec","off"]
    order = settings.get("profile_order", allowed_profiles)
    if not isinstance(order, list):
        order = allowed_profiles
    clean = []
    for item in order:
        if item in allowed_profiles and item not in clean:
            clean.append(item)
    for item in allowed_profiles:
        if item not in clean:
            clean.append(item)
    settings["profile_order"] = clean

    if not save_settings(settings):
        return jsonify({"ok":False,"error":"Could not save settings"}),500

    apply_reboot_cron(settings)
    return jsonify({"ok":True,"settings":settings})

@app.route("/api/action",methods=["POST"])
def action():
    auth = require_control_auth()
    if auth: return auth
    a=request.get_json(force=True).get("action","")
    if blocked_when_locked() and a not in ["backup_dashboard"]:
        return jsonify({"ok":False,"error":"Dashboard is locked or read-only"}),403
    if a=="restart_hotspot":
        out=run(["sh","-c","nmcli connection down Hotspot; sleep 2; nmcli connection up Hotspot"],20)
        return jsonify({"ok":True,"message":"Hotspot restarted","output":out})
    if a=="backup_dashboard":
        out=run(["cp","/opt/vpn-dashboard/app.py",DASHBOARD_BACKUP],8)
        return jsonify({"ok":True,"message":"Dashboard backed up locally","output":out})
    if a=="restore_dashboard_backup":
        if not os.path.exists(DASHBOARD_BACKUP):
            return jsonify({"ok":False,"error":"No local dashboard backup found"}),400
        out=run(["cp",DASHBOARD_BACKUP,"/opt/vpn-dashboard/app.py"],8)
        subprocess.Popen(["/bin/sh","-c","sleep 1; systemctl restart vpn-dashboard.service"])
        return jsonify({"ok":True,"message":"Restoring dashboard backup...","output":out})
    if a=="safe_mode":
        resume_internet()
        run(["systemctl","stop","proton-port.service"],8)
        run(["systemctl","stop","wg-quick@wg0"],8)
        run(["nmcli","connection","down","Hotspot"],10)
        run(["systemctl","restart","NetworkManager"],15)
        try:
            with open("/run/vpn-profile-current","w") as f:
                f.write("off")
        except Exception:
            pass
        return jsonify({"ok":True,"message":"Safe mode activated: VPN off, hotspot down, NetworkManager restarted"})
    if a=="renew_p2p":
        if read("/run/vpn-profile-current") != "p2p":
            return jsonify({"ok":False,"error":"P2P profile is not active"}),400
        run(["ip","route","replace","10.2.0.1","dev","wg0"],4)
        run(["systemctl","restart","proton-port.service"],8)
        return jsonify({"ok":True,"message":"P2P port renewal requested"})
    if a=="pause_internet":
        pause_internet()
        return jsonify({"ok":True,"message":"Client internet paused"})
    if a=="resume_internet":
        resume_internet()
        return jsonify({"ok":True,"message":"Client internet resumed"})
    if a=="reboot":
        subprocess.Popen(["/bin/sh","-c","sleep 2; reboot"])
        return jsonify({"ok":True,"message":"Rebooting Pi..."})
    return jsonify({"ok":False,"error":"Unknown action"}),400

@app.route("/api/dns-test",methods=["POST"])
def api_dns_test():
    return jsonify(dns_test())

@app.route("/api/client-name", methods=["POST"])
def api_client_name():
    auth = require_control_auth()
    if auth: return auth
    data = request.get_json(force=True)
    mac = data.get("mac", "")
    ip = data.get("ip", "")
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"ok":False,"error":"Name cannot be blank"}),400
    if save_client_name(mac, ip, name):
        return jsonify({"ok":True})
    return jsonify({"ok":False,"error":"Could not save client name"}),500


@app.route("/api/change-password", methods=["POST"])
def api_change_password():
    auth = require_control_auth()
    if auth: return auth

    data = request.get_json(force=True)
    old_password = data.get("old_password", "")
    new_password = data.get("new_password", "")

    if len(new_password) < 8:
        return jsonify({"ok":False,"error":"New password must be at least 8 characters"}),400

    auth_data = load_auth()
    pw_hash = auth_data.get("password_hash")

    if pw_hash and not check_password_hash(pw_hash, old_password):
        return jsonify({"ok":False,"error":"Current password is wrong"}),403

    auth_data["password_hash"] = generate_password_hash(new_password)

    # Keep trusted devices, but you can clear them manually later if desired.
    auth_data.setdefault("trusted_devices", {})

    if save_auth(auth_data):
        return jsonify({"ok":True,"message":"Password changed"})
    return jsonify({"ok":False,"error":"Could not save password"}),500


@app.route("/api/device-control", methods=["POST"])
def api_device_control():
    auth = require_control_auth()
    if auth: return auth

    data = request.get_json(force=True)
    action = data.get("action")
    ip = data.get("ip")
    mbps = data.get("mbps", "")

    if not re.match(r"^10\.42\.\d{1,3}\.\d{1,3}$", ip or ""):
        return jsonify({"ok":False,"error":"Invalid IP"}),400

    if action in ["block", "unblock"]:
        out = run(["/usr/local/sbin/protonpi-device-control", action, ip], 10)
        if action == "block":
            save_client_policy(ip, "Blocked")
        elif action == "unblock":
            save_client_policy(ip, None)
        run(["/usr/local/sbin/protonpi-apply-limits"], 10)
        return jsonify({"ok":True,"output":out})

    if action == "prioritize":
        save_client_policy(ip, "Prioritized")
        return jsonify({"ok":True,"message":"Device marked as prioritized"})

    if action == "limit":
        try:
            value = float(mbps)
            if value <= 0:
                raise ValueError()
        except Exception:
            return jsonify({"ok":False,"error":"Invalid Mbps limit"}),400
        save_client_policy(ip, f"Limit: {value:g} Mbps")
        out = run(["/usr/local/sbin/protonpi-apply-limits"], 10)
        return jsonify({"ok":True,"message":f"Download limit applied: {value:g} Mbps","output":out})

    if action == "clear_limit":
        save_client_policy(ip, None)
        out = run(["/usr/local/sbin/protonpi-apply-limits"], 10)
        return jsonify({"ok":True,"message":"Device policy cleared","output":out})

    return jsonify({"ok":False,"error":"Invalid action"}),400


@app.route("/api/speedtest",methods=["POST"])
def speedtest():
    cmd="curl -4 -L --max-time 15 -o /dev/null -s -w '%{speed_download}' https://speed.cloudflare.com/__down?bytes=5000000"
    val=run(["sh","-c",cmd],18).strip()
    try:
        mbps=float(val)*8/1000000
        return jsonify({"ok":True,"result":f"Approx download: {mbps:.1f} Mbps"})
    except:
        return jsonify({"ok":False,"error":"Speed test failed"})

@app.route("/settings.json")
def export_settings():
    auth = require_control_auth()
    if auth: return auth
    settings=load_settings()
    settings["default_profile"]=default_profile()
    return jsonify(settings)


@app.route("/backup.tar.gz")
def backup_pack():
    auth = require_control_auth()
    if auth: return auth
    path = run(["/usr/local/sbin/protonpi-backup"], 60).strip().splitlines()[-1]
    try:
        data = open(path, "rb").read()
        return Response(data, mimetype="application/gzip", headers={
            "Content-Disposition": f"attachment; filename={os.path.basename(path)}"
        })
    except Exception as e:
        return Response(f"Backup failed: {e}", status=500)

@app.route("/readonly")
def readonly():
    return render_template_string(HTML.replace("VPN control", "Read-only status").replace("Tools", "Read-only tools"))

@app.route("/api/lock", methods=["POST"])
def api_lock():
    data = request.get_json(force=True)
    settings = load_settings()
    if "dashboard_locked" in data:
        settings["dashboard_locked"] = bool(data["dashboard_locked"])
    if "read_only_mode" in data:
        settings["read_only_mode"] = bool(data["read_only_mode"])
    save_settings(settings)
    return jsonify({"ok": True, "settings": settings})


@app.route("/api/backups")
def api_backups():
    auth = require_control_auth()
    if auth: return auth

    os.makedirs(BACKUP_DIR, exist_ok=True)
    items = []
    for name in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if not name.endswith(".tar.gz"):
            continue
        path = os.path.join(BACKUP_DIR, name)
        try:
            st = os.stat(path)
            items.append({
                "name": name,
                "size": st.st_size,
                "mtime": int(st.st_mtime)
            })
        except Exception:
            pass
    return jsonify({"ok": True, "backups": items})

@app.route("/api/create-backup", methods=["POST"])
def api_create_backup():
    auth = require_control_auth()
    if auth: return auth

    out = run(["/usr/local/sbin/protonpi-backup"], 60).strip()
    return jsonify({"ok": True, "path": out, "name": os.path.basename(out)})

@app.route("/api/restore-backup", methods=["POST"])
def api_restore_backup():
    auth = require_control_auth()
    if auth: return auth

    data = request.get_json(force=True)
    name = data.get("name", "")

    if "/" in name or ".." in name or not name.endswith(".tar.gz"):
        return jsonify({"ok": False, "error": "Invalid backup name"}), 400

    path = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": "Backup not found"}), 404

    out = run(["/usr/local/sbin/protonpi-restore", path], 90)
    return jsonify({"ok": True, "message": "Backup restored. Dashboard services restarted.", "output": out})

@app.route("/backup-file/<name>")
def download_backup_file(name):
    auth = require_control_auth()
    if auth: return auth

    if "/" in name or ".." in name or not name.endswith(".tar.gz"):
        return Response("Invalid backup name", status=400)

    path = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(path):
        return Response("Backup not found", status=404)

    data = open(path, "rb").read()
    return Response(data, mimetype="application/gzip", headers={
        "Content-Disposition": f"attachment; filename={name}"
    })

@app.route("/diagnostics.txt")
def diagnostics():
    auth = require_control_auth()
    if auth: return auth
    data=[]
    for title,cmd in [
        ("date",["date"]),("ip route",["ip","route"]),("nmcli device",["nmcli","device","status"]),
        ("wg",["wg"]),("vpn-profile status",["/usr/local/sbin/vpn-profile","status"]),
        ("dashboard status",["systemctl","status","vpn-dashboard.service","--no-pager"]),
        ("watchdog status",["systemctl","status","vpn-watchdog.timer","--no-pager"]),
        ("proton-port status",["systemctl","status","proton-port.service","--no-pager"]),
        ("iptables forward",["iptables","-vnx","-L","FORWARD"]),
        ("disk",["df","-h"]),("memory",["free","-h"])
    ]:
        data.append(f"\n===== {title} =====\n{run(cmd,8)}\n")
    return Response("".join(data),mimetype="text/plain")


@app.route("/protonpi.crt")
def protonpi_cert():
    try:
        data = open("/etc/protonpi-dashboard/certs/protonpi.crt", "rb").read()
        return Response(data, mimetype="application/x-x509-ca-cert", headers={
            "Content-Disposition": "attachment; filename=protonpi.crt"
        })
    except Exception as e:
        return Response(f"Certificate not found: {e}", status=500)

@app.route("/wifi-qr.png")
def wifi_qr():
    ssid="Pi-VPN-Router"
    password="Password"
    wifi=f"WIFI:T:WPA;S:{ssid};P:{password};;"
    png=run(["sh","-c",f"qrencode -t PNG -o - '{wifi}' | base64 -w0"],5)
    return Response(base64.b64decode(png),mimetype="image/png")

if __name__=="__main__":
    ensure_dirs()
    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    ssl_cert = os.environ.get("DASHBOARD_SSL_CERT")
    ssl_key = os.environ.get("DASHBOARD_SSL_KEY")

    if ssl_cert and ssl_key:
        app.run(
            host="10.42.0.1",
            port=port,
            ssl_context=(ssl_cert, ssl_key)
        )
    else:
        app.run(host="10.42.0.1", port=port)
