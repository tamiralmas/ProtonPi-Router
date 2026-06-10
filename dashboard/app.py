#!/usr/bin/env python3
from flask import Flask, jsonify, request, render_template_string, Response, redirect, make_response, session
import subprocess, os, time, re, json, base64, socket, uuid, secrets, shutil
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
    if val == "off":
        return "off"
    if val and profile_exists_for_activation(val):
        return val
    return "gaming"

def set_default_profile(profile):
    if not profile_exists_for_activation(profile):
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
    # Fixed short sampling window so the reading does not depend on how long
    # since the last call (which made it spike right after a page reload).
    try:
        def _snap():
            with open("/proc/stat") as f:
                vals=list(map(int,f.readline().split()[1:]))
            return sum(vals), vals[3]+vals[4]
        t1,i1=_snap()
        time.sleep(0.18)
        t2,i2=_snap()
        dt=t2-t1; di=i2-i1
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
        name=aliases.get(mac.lower()) or aliases.get(ip) or f"Device {ip}"
        if ip.startswith("10.42.") or mac!="unknown":
            rows.append({"ip":ip,"mac":mac,"name":name,"state":state})
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


HTML2 = r"""<!DOCTYPE html>
<html lang="en" data-theme="blue">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ProtonPi</title>
<style>
  :root{
    --bg0:#05070d; --bg1:#080b14; --card:#0f1626; --card2:#16203a;
    --text:#eef3fb; --muted:#8a99b3; --line:rgba(255,255,255,.09);
    --primary:#4f8cff; --primary2:#7aa7ff; --good:#35d07f; --warn:#ffd166; --bad:#ff5b6e;
    --mono:ui-monospace,"SF Mono",SFMono-Regular,"JetBrains Mono",Menlo,Consolas,monospace;
    --sans:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,Arial,sans-serif;
  }
  html[data-theme="green"]{ --primary:#35d07f; --primary2:#7ff0b1; --card:#0c2018; --card2:#11301f; }
  html[data-theme="purple"]{ --primary:#9b6cff; --primary2:#c6a8ff; --card:#181230; --card2:#241a45; }
  html[data-theme="red"]{ --primary:#ff5b6e; --primary2:#ff9aa6; --card:#241016; --card2:#341a24; }
  *{ box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  @media (prefers-reduced-motion: reduce){ *{ animation:none !important; transition:none !important; } }
  body{ margin:0; color:var(--text); font-family:var(--sans); line-height:1.45;
    background:
      radial-gradient(900px 480px at 12% -8%, color-mix(in srgb,var(--primary) 22%, transparent), transparent 60%),
      radial-gradient(760px 420px at 100% 0%, color-mix(in srgb,var(--primary2) 16%, transparent), transparent 55%),
      linear-gradient(180deg,var(--bg1),var(--bg0)); min-height:100vh; }
  .wrap{ max-width:1080px; margin:0 auto; padding:0 14px 48px; }

  .cmd{ position:sticky; top:0; z-index:30; margin:0 -14px 16px; padding:10px 16px; border-bottom:1px solid var(--line);
    background: repeating-linear-gradient(180deg, rgba(255,255,255,.018) 0 1px, transparent 1px 3px), color-mix(in srgb,var(--bg0) 78%, transparent);
    backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px); }
  .cmdIn{ max-width:1080px; margin:0 auto; display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
  .brand{ display:flex; align-items:baseline; gap:9px; margin-right:auto; }
  .brand b{ font-size:19px; letter-spacing:-.4px; }
  .brand span{ font-family:var(--mono); font-size:11px; color:var(--muted); letter-spacing:.5px; text-transform:uppercase; }
  .state{ display:flex; align-items:center; gap:9px; font-family:var(--mono); font-size:13px; font-weight:600; }
  .dot{ width:9px; height:9px; border-radius:50%; background:var(--good); box-shadow:0 0 0 0 color-mix(in srgb,var(--good) 70%,transparent); animation:pulse 2.4s infinite; }
  .dot.off{ background:var(--bad); animation:none; }
  @keyframes pulse{ 0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--good) 55%,transparent);} 70%{box-shadow:0 0 0 7px transparent;} 100%{box-shadow:0 0 0 0 transparent;} }
  .chip{ font-family:var(--mono); font-size:12px; font-weight:700; padding:5px 10px; border-radius:999px;
    background:color-mix(in srgb,var(--primary) 16%,transparent); border:1px solid color-mix(in srgb,var(--primary) 40%,transparent); color:var(--primary2); white-space:nowrap; }
  .rate{ font-family:var(--mono); font-size:12px; color:var(--muted); white-space:nowrap; }
  .rate b{ color:var(--text); }
  .powerWrap{ position:relative; }
  .powerBtn{ width:38px; height:38px; border-radius:50%; display:grid; place-items:center; cursor:pointer; color:var(--text); background:rgba(255,255,255,.08); border:1px solid var(--line); }
  .powerBtn:active{ transform:scale(.96); } .powerBtn svg{ width:18px; height:18px; }
  .menu{ position:absolute; right:0; top:46px; min-width:204px; z-index:40; display:none; background:linear-gradient(180deg,var(--card2),var(--card)); border:1px solid var(--line); border-radius:14px; padding:6px; box-shadow:0 18px 44px rgba(0,0,0,.5); }
  .menu.show{ display:block; }
  .menu button{ display:flex; width:100%; align-items:center; text-align:left; font:inherit; font-size:13px; font-weight:700; color:var(--text); background:transparent; border:0; padding:11px 12px; border-radius:9px; cursor:pointer; }
  .menu button:hover{ background:rgba(255,255,255,.06); } .menu button.danger{ color:#ffb3bc; }
  .menu .sep{ height:1px; background:var(--line); margin:5px 4px; }

  .banner{ display:none; margin-bottom:14px; padding:11px 14px; border-radius:13px; font-size:13px; font-weight:600;
    background:color-mix(in srgb,var(--warn) 16%,transparent); border:1px solid color-mix(in srgb,var(--warn) 40%,transparent); color:#ffe6a6; }
  .banner a{ color:#fff; font-weight:900; }

  .tabs{ display:flex; gap:6px; overflow-x:auto; padding:4px; margin-bottom:16px; background:rgba(255,255,255,.035); border:1px solid var(--line); border-radius:16px; scrollbar-width:none; }
  .tabs::-webkit-scrollbar{ display:none; }
  .tab{ flex:1 0 auto; min-width:max-content; text-align:center; font:inherit; font-weight:800; font-size:14px; padding:10px 16px; border-radius:12px; border:0; background:transparent; color:var(--muted); cursor:pointer; white-space:nowrap; }
  .tab[aria-selected="true"]{ color:var(--text); background:linear-gradient(180deg,color-mix(in srgb,var(--primary) 26%,transparent),color-mix(in srgb,var(--primary) 12%,transparent)); box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--primary) 35%,transparent); }
  .panel{ display:none; animation:fade .25s ease; } .panel.active{ display:block; }
  @keyframes fade{ from{opacity:0; transform:translateY(4px);} to{opacity:1; transform:none;} }

  .grid{ display:grid; gap:13px; grid-template-columns:repeat(2,1fr); }
  .grid .span2{ grid-column:1 / -1; }
  @media (max-width:760px){ .grid{ grid-template-columns:1fr; } .grid .span2{ grid-column:auto; } }
  .card{ background:linear-gradient(180deg,color-mix(in srgb,var(--card2) 80%,transparent),color-mix(in srgb,var(--card) 92%,transparent)); border:1px solid var(--line); border-radius:20px; padding:16px; box-shadow:0 16px 40px rgba(0,0,0,.32); }
  .card h2{ margin:0 0 13px; font-size:13px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); font-weight:800; }
  .eyebrow{ font-family:var(--mono); font-size:11px; color:var(--muted); letter-spacing:.08em; text-transform:uppercase; }
  .big{ font-size:30px; letter-spacing:-1px; margin-top:4px; font-family:var(--mono); font-weight:700; }
  .heroRow{ display:flex; gap:10px; flex-wrap:wrap; }
  .heroStat{ flex:1 1 130px; background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.06); border-radius:14px; padding:12px; }
  .heroStat .v{ font-family:var(--mono); font-size:18px; font-weight:700; margin-top:5px; word-break:break-word; }
  .heroStat.clickable{ cursor:pointer; transition:border-color .15s, background .15s; }
  .heroStat.clickable:hover{ border-color:color-mix(in srgb,var(--primary) 45%,transparent); background:rgba(255,255,255,.06); }
  .hintp{ font-family:var(--mono); font-size:10px; color:var(--muted); margin-top:4px; opacity:.65; }

  .pbtns{ display:grid; grid-template-columns:repeat(auto-fit,minmax(132px,1fr)); gap:10px; }
  .pbtn{ font:inherit; font-weight:800; font-size:14px; min-height:50px; border-radius:14px; cursor:pointer; color:#fff; border:1px solid rgba(255,255,255,.14); padding:8px; }
  .pbtn:active{ transform:scale(.98); }
  .pbtn.gaming{ background:linear-gradient(135deg,#2f80ff,#5aa2ff); } .pbtn.p2p{ background:linear-gradient(135deg,#13a664,#35d07f); }
  .pbtn.streaming{ background:linear-gradient(135deg,#00a6ff,#00d4ff); } .pbtn.maxsec{ background:linear-gradient(135deg,#7c4dff,#b085ff); }
  .pbtn.active{ outline:2px solid rgba(255,255,255,.8); box-shadow:0 0 0 4px rgba(255,255,255,.10); }
  .note{ margin-top:12px; padding:11px; border-radius:13px; background:rgba(255,255,255,.04); border:1px solid var(--line); color:var(--muted); font-size:13px; }

  .dev{ background:rgba(255,255,255,.04); border:1px solid var(--line); border-radius:14px; padding:12px; margin-bottom:10px; cursor:pointer; }
  .dev:last-child{ margin-bottom:0; }
  .devTop{ display:flex; justify-content:space-between; align-items:center; gap:12px; }
  .devName{ font-weight:800; } .devMeta{ font-family:var(--mono); font-size:12px; color:var(--muted); margin-top:2px; }
  .chev{ width:26px; height:26px; flex:0 0 26px; display:grid; place-items:center; border-radius:50%; background:rgba(255,255,255,.07); border:1px solid var(--line); color:var(--muted); transition:transform .2s; }
  .dev.open .chev{ transform:rotate(180deg); color:var(--text); }
  .devBody{ display:none; margin-top:11px; gap:9px; grid-template-columns:1fr 1fr; }
  .dev.open .devBody{ display:grid; }
  .kv{ background:rgba(255,255,255,.035); border:1px solid var(--line); border-radius:11px; padding:9px; }
  .kv .k{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }
  .kv .v{ font-family:var(--mono); font-weight:700; margin-top:3px; }

  label.f{ display:block; font-size:12px; color:var(--muted); margin-bottom:10px; }
  label.f:last-child{ margin-bottom:0; }
  input,select{ width:100%; margin-top:6px; font:inherit; font-size:14px; color:var(--text); padding:11px 12px; border-radius:12px; background:rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.12); }
  input:focus,select:focus,button:focus-visible,.tab:focus-visible{ outline:2px solid var(--primary2); outline-offset:2px; }
  option{ background:#0f1626; }
  .btn{ font:inherit; font-weight:800; font-size:14px; padding:12px 14px; border-radius:13px; cursor:pointer; color:#fff; background:linear-gradient(135deg,var(--primary),var(--primary2)); border:0; }
  .btn.ghost{ background:rgba(255,255,255,.07); border:1px solid var(--line); } .btn.danger{ background:color-mix(in srgb,var(--bad) 16%,transparent); border:1px solid color-mix(in srgb,var(--bad) 40%,transparent); color:#ffd9de; }
  .btn:active{ transform:scale(.98); } .btn:disabled,.pbtn:disabled,.powerBtn:disabled{ opacity:.5; cursor:not-allowed; }
  .status{ font-family:var(--mono); font-size:12px; color:var(--muted); margin-top:10px; }

  .qr{ width:140px; height:140px; border-radius:14px; background:#fff; padding:8px; }
  .swatch{ width:34px; height:34px; border-radius:10px; border:1px solid var(--line); cursor:pointer; }
  .swatch[aria-pressed="true"]{ outline:2px solid var(--text); outline-offset:2px; }
  .palette{ display:flex; gap:10px; flex-wrap:wrap; }
  .filepick{ position:relative; display:flex; align-items:center; gap:12px; margin-top:6px; flex-wrap:wrap; }
  .filepick input[type="file"]{ position:absolute; inset:0; width:100%; height:100%; opacity:0; cursor:pointer; margin:0; padding:0; }
  .filebtn{ display:inline-block; font-weight:800; font-size:13px; padding:11px 16px; border-radius:12px; background:rgba(255,255,255,.08); border:1px solid var(--line); color:var(--text); white-space:nowrap; }
  .fileName{ font-family:var(--mono); font-size:12px; color:var(--muted); }

  canvas{ width:100%; height:150px; display:block; }
  .graphWrap{ position:relative; }
  .gtip{ position:absolute; top:6px; display:none; transform:translateX(-50%); pointer-events:none; background:color-mix(in srgb,var(--bg0) 92%,transparent); border:1px solid var(--line); border-radius:10px; padding:7px 10px; font-family:var(--mono); font-size:11px; line-height:1.5; color:var(--text); white-space:nowrap; box-shadow:0 6px 18px rgba(0,0,0,.4); z-index:5; }
  .gtip b{ color:var(--primary2); }
  .legend{ display:flex; gap:16px; margin-top:8px; font-family:var(--mono); font-size:12px; color:var(--muted); }
  .ldot{ width:9px; height:9px; border-radius:50%; display:inline-block; margin-right:6px; vertical-align:middle; }

  .modal{ position:fixed; inset:0; z-index:50; display:none; align-items:center; justify-content:center; padding:16px; background:rgba(0,0,0,.6); backdrop-filter:blur(4px); }
  .modal.show{ display:flex; }
  .sheet{ width:100%; max-width:560px; background:linear-gradient(180deg,var(--card2),var(--card)); border:1px solid var(--line); border-radius:20px; padding:18px; box-shadow:0 24px 60px rgba(0,0,0,.5); }
  .sheetTop{ display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:12px; } .sheetTop h3{ margin:0; font-size:16px; }
  .x{ border:0; background:rgba(255,255,255,.08); color:var(--text); width:34px; height:34px; border-radius:50%; cursor:pointer; font-size:18px; line-height:1; }
  .mstat{ font-family:var(--mono); font-size:13px; color:var(--muted); margin-top:10px; } .mstat b{ color:var(--text); font-size:20px; }
  .toast{ position:fixed; left:50%; bottom:24px; transform:translateX(-50%) translateY(10px); opacity:0; pointer-events:none; background:color-mix(in srgb,var(--primary) 24%, var(--card)); border:1px solid color-mix(in srgb,var(--primary) 45%,transparent); color:var(--text); font-weight:700; font-size:13px; padding:10px 16px; border-radius:999px; z-index:60; transition:.2s; }
  .toast.show{ opacity:1; transform:translateX(-50%) translateY(0); }
  @media (max-width:760px){ .card h2{ text-align:center; } .heroStat{ text-align:center; } .big, .note, .status, .eyebrow{ text-align:center; } .legend, .palette{ justify-content:center; } }
  @media (max-width:560px){ .modal{ align-items:flex-end; padding:0; } .sheet{ border-radius:20px 20px 0 0; max-width:none; } .cmdIn{ justify-content:center; } .brand{ flex:1 1 100%; justify-content:center; text-align:center; margin-right:0; } .big{ font-size:25px; } }
</style>
</head>
<body>
<div class="cmd">
  <div class="cmdIn">
    <div class="brand"><b>ProtonPi</b><span>router</span></div>
    <div class="state"><span class="dot" id="dot"></span><span id="stateText">Checking</span></div>
    <span class="chip" id="profChip">—</span>
    <span class="rate">&#8595; <b id="rxRate">0.0</b> &#183; &#8593; <b id="txRate">0.0</b> Mb/s</span>
    <div class="powerWrap">
      <button class="powerBtn" id="powerBtn" data-control onclick="togglePower(event)" aria-label="Power options" title="Power options">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 3v9"/><path d="M6.5 7a8 8 0 1 0 11 0"/></svg>
      </button>
      <div class="menu" id="powerMenu" role="menu">
        <button role="menuitem" onclick="confirmAction('vpnoff')">Turn off VPN</button>
        <button role="menuitem" onclick="confirmAction('hotspot')">Restart hotspot</button>
        <div class="sep"></div>
        <button role="menuitem" class="danger" onclick="confirmAction('reboot')">Reboot computer</button>
        <button role="menuitem" class="danger" onclick="confirmAction('shutdown')">Shutdown computer</button>
      </div>
    </div>
  </div>
</div>

<div class="wrap">
  <div id="loginBanner" class="banner">Not signed in &#8212; status is visible but controls are disabled. <a href="/login">Login</a></div>

  <div class="tabs" role="tablist">
    <button class="tab" role="tab" aria-selected="true" onclick="showTab(this,'overview')">Overview</button>
    <button class="tab" role="tab" aria-selected="false" onclick="showTab(this,'profiles')">Profiles</button>
    <button class="tab" role="tab" aria-selected="false" onclick="showTab(this,'devices')">Devices</button>
    <button class="tab" role="tab" aria-selected="false" onclick="showTab(this,'network')">Network</button>
  </div>

  <section class="panel active" id="overview">
    <div class="grid">
      <div class="card span2">
        <div class="eyebrow">Tunnel</div>
        <div class="big" id="heroState">Checking</div>
        <div class="heroRow" style="margin-top:14px;">
          <div class="heroStat"><div class="eyebrow">Exit IP</div><div class="v" id="tExitIp">&#8212;</div></div>
          <div class="heroStat"><div class="eyebrow">Exit node</div><div class="v" id="tNode">&#8212;</div></div>
          <div class="heroStat"><div class="eyebrow">Handshake</div><div class="v" id="tHandshake">&#8212;</div></div>
          <div class="heroStat"><div class="eyebrow">Uptime</div><div class="v" id="tUptime">&#8212;</div></div>
          <div class="heroStat"><div class="eyebrow">Latency</div><div class="v" id="tLatency">&#8212;</div></div>
          <div class="heroStat"><div class="eyebrow">Packet loss</div><div class="v" id="tLoss">&#8212;</div></div>
          <div class="heroStat"><div class="eyebrow">DNS</div><div class="v" id="tDns">&#8212;</div></div>
          <div class="heroStat"><div class="eyebrow">Port forwarding</div><div class="v" id="pfVal" style="color:var(--muted)">&#8212;</div></div>
        </div>
      </div>

      <div class="card span2">
        <h2>Traffic</h2>
        <div class="heroRow">
          <div class="heroStat"><div class="eyebrow">Downloaded</div><div class="v" id="totDown">&#8212;</div></div>
          <div class="heroStat"><div class="eyebrow">Uploaded</div><div class="v" id="totUp">&#8212;</div></div>
          <div class="heroStat"><div class="eyebrow">Session</div><div class="v" id="totSession">&#8212;</div></div>
        </div>
        <div class="graphWrap" style="margin-top:14px"><canvas id="graph"></canvas><div class="gtip" id="gtip"></div></div>
        <div class="legend"><span><span class="ldot" style="background:var(--primary)"></span>Down</span><span><span class="ldot" style="background:var(--good)"></span>Up</span></div>
      </div>

      <div class="card span2">
        <h2>Machine</h2>
        <div class="heroRow">
          <div class="heroStat clickable" onclick="openMetric('CPU temp')"><div class="eyebrow">CPU temp</div><div class="v" id="mTemp">&#8212;</div><div class="hintp">tap for graph</div></div>
          <div class="heroStat clickable" onclick="openMetric('CPU load')"><div class="eyebrow">CPU load</div><div class="v" id="mCpu">&#8212;</div><div class="hintp">tap for graph</div></div>
          <div class="heroStat clickable" onclick="openMetric('Memory')"><div class="eyebrow">Memory</div><div class="v" id="mMem">&#8212;</div><div class="hintp">tap for graph</div></div>
          <div class="heroStat"><div class="eyebrow">Storage</div><div class="v" id="mDisk">&#8212;</div></div>
        </div>
      </div>

      <div class="card span2">
        <h2>Speed test</h2>
        <div class="heroRow">
          <div class="heroStat"><div class="eyebrow">Download</div><div class="v" id="stDown">&#8212;</div></div>
          <div class="heroStat"><div class="eyebrow">Result</div><div class="v" id="stResult" style="font-size:13px">&#8212;</div></div>
        </div>
        <button class="btn" id="stBtn" data-control style="width:100%;margin-top:12px" onclick="runSpeedTest()">Run speed test</button>
        <div class="status" id="stStatus">Approx download speed through the active tunnel.</div>
      </div>
    </div>
  </section>

  <section class="panel" id="profiles">
    <div class="grid">
      <div class="card span2">
        <h2>VPN control</h2>
        <div class="pbtns" id="pbtns"></div>
        <div class="note" id="pnote">&#8212;</div>
      </div>
      <div class="card span2">
        <h2>Profile details &amp; management</h2>
        <label class="f">Profile<select id="detSel" onchange="showConf()"></select></label>
        <div class="heroRow" id="confGrid" style="margin-top:2px"></div>
        <button class="btn danger" data-control style="width:100%;margin-top:14px" onclick="deleteSelected()">Delete selected profile</button>
        <div class="status">Public details only &#8212; private and preshared keys are never shown.</div>
      </div>
      <div class="card span2">
        <h2>Import profile</h2>
        <label class="f">WireGuard .conf
          <div class="filepick">
            <input type="file" id="confFile" accept=".conf,.txt" data-control onchange="document.getElementById('fileName').textContent = this.files[0] ? this.files[0].name : 'No file chosen'">
            <span class="filebtn">Choose .conf file</span>
            <span class="fileName" id="fileName">No file chosen</span>
          </div>
        </label>
        <div class="eyebrow" style="margin:2px 0 9px">Button color</div>
        <div class="palette" id="importPalette">
          <button type="button" class="swatch" data-c="#4f8cff" style="background:#4f8cff"></button>
          <button type="button" class="swatch" data-c="#35d07f" style="background:#35d07f"></button>
          <button type="button" class="swatch" data-c="#00d4ff" style="background:#00d4ff"></button>
          <button type="button" class="swatch" data-c="#9b6cff" style="background:#9b6cff"></button>
          <button type="button" class="swatch" data-c="#ff7a1a" style="background:#ff7a1a" aria-pressed="true"></button>
          <button type="button" class="swatch" data-c="#ff5b6e" style="background:#ff5b6e"></button>
          <button type="button" class="swatch" data-c="#ffd166" style="background:#ffd166"></button>
        </div>
        <button class="btn" data-control style="width:100%;margin-top:14px" onclick="importVpnConfig()">Import profile</button>
        <div class="status" id="importStatus">Validates [Interface] / [Peer], rejects PostUp / PreUp / PostDown / PreDown.</div>
      </div>
    </div>
  </section>

  <section class="panel" id="devices">
    <div class="card">
      <h2 id="devTitle">Connected devices</h2>
      <div id="deviceList"><div class="status">Loading&#8230;</div></div>
    </div>
  </section>

  <section class="panel" id="network">
    <div class="grid">
      <div class="card span2">
        <h2>Wi-Fi settings</h2>
        <div class="grid" style="gap:12px">
          <label class="f">Network name (SSID)<input id="wifiSsid" data-control></label>
          <label class="f">Password<input id="wifiPassword" type="text" data-control placeholder="Leave blank to keep current"></label>
          <label class="f">Security
            <select id="wifiSecurity" data-control><option value="wpa-psk">WPA2 (recommended)</option><option value="wpa-psk sae">WPA2 / WPA3 mixed</option><option value="sae">WPA3 only</option></select>
          </label>
          <label class="f">Band
            <select id="wifiBand" data-control onchange="populateWifiChannels()"><option value="auto">Auto (best band)</option><option value="a">5 GHz</option><option value="bg">2.4 GHz</option></select>
          </label>
          <label class="f">Channel<select id="wifiChannel" data-control></select></label>
          <div style="display:flex;align-items:end"><button class="btn" data-control style="width:100%" onclick="applyWifi()">Apply Wi-Fi settings</button></div>
        </div>
        <div class="status" id="wifiStatus">&#8212;</div>
        <div class="status" style="opacity:.8">Applying restarts the hotspot and disconnects wireless clients. Manage over Ethernet to avoid losing access. WPA3 and Auto band need driver support and may fail on some adapters.</div>
        <div style="display:flex;gap:16px;align-items:center;margin-top:16px;flex-wrap:wrap">
          <img class="qr" id="wifiQr" src="/wifi-qr.png" alt="Wi-Fi QR">
          <div class="status" style="margin:0">Scan to join the hotspot. The QR reflects the live SSID and key.</div>
        </div>
      </div>

      <div class="card">
        <h2>Appearance</h2>
        <div class="eyebrow" style="margin-bottom:9px">Accent</div>
        <div class="palette">
          <button class="swatch" data-theme-c="blue" style="background:#4f8cff" onclick="setTheme('blue')"></button>
          <button class="swatch" data-theme-c="green" style="background:#35d07f" onclick="setTheme('green')"></button>
          <button class="swatch" data-theme-c="purple" style="background:#9b6cff" onclick="setTheme('purple')"></button>
          <button class="swatch" data-theme-c="red" style="background:#ff5b6e" onclick="setTheme('red')"></button>
        </div>
      </div>

      <div class="card">
        <h2>Scheduled reboot</h2>
        <label class="f">Day<select id="rebootDay" data-control onchange="saveReboot()"><option value="off">Off</option><option value="daily">Daily</option><option value="sunday">Sunday</option></select></label>
        <label class="f">Time<input id="rebootTime" type="time" data-control value="04:00" onchange="saveReboot()"></label>
      </div>

      <div class="card">
        <h2>Access</h2>
        <button class="btn ghost" data-control style="width:100%" onclick="changePassword()">Change dashboard password</button>
      </div>
    </div>
  </section>
</div>

<div class="modal" id="metricModal" onclick="if(event.target===this)closeMetric()">
  <div class="sheet">
    <div class="sheetTop"><h3 id="mTitle">Metric</h3><button class="x" onclick="closeMetric()" aria-label="Close">&#215;</button></div>
    <div class="graphWrap"><canvas id="mGraph" style="height:180px"></canvas><div class="gtip" id="mtip"></div></div>
    <div class="mstat">Now: <span id="mNow"><b>&#8212;</b></span></div>
  </div>
</div>

<div class="modal" id="confirmModal" onclick="if(event.target===this)cancelConfirm()">
  <div class="sheet" style="max-width:420px">
    <div class="sheetTop"><h3 id="cfTitle">Are you sure?</h3><button class="x" onclick="cancelConfirm()" aria-label="Close">&#215;</button></div>
    <div id="cfMsg" style="font-size:14px; color:var(--text); margin-bottom:16px;"></div>
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
      <button class="btn ghost" onclick="cancelConfirm()">Cancel</button>
      <button class="btn" id="cfOk" onclick="runConfirm()">Confirm</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const $ = id => document.getElementById(id);
function setText(id,v){ const el=$(id); if(el) el.textContent=(v==null||v==="")?"\u2014":v; }
function cssVar(n){ return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function fmtBytes(n){ if(n==null) return "\u2014"; n=+n; const u=["B","KB","MB","GB","TB"]; let i=0; while(n>=1024&&i<u.length-1){n/=1024;i++;} return n.toFixed(i?1:0)+" "+u[i]; }
function mbps(bps){ return bps==null ? "0.0" : (bps*8/1e6).toFixed(1); }
function agoFmt(sec){ sec=+sec; if(sec<60) return sec+"s ago"; if(sec<3600) return Math.floor(sec/60)+"m ago"; return Math.floor(sec/3600)+"h ago"; }

function showTab(btn,id){
  document.querySelectorAll('.tab').forEach(t=>t.setAttribute('aria-selected', t===btn));
  document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active', p.id===id));
  if(id==='network') loadWifi();
  window.scrollTo({top:0,behavior:'smooth'});
}

/* theme */
function setTheme(t){ document.documentElement.setAttribute('data-theme', t); try{ localStorage.setItem('theme', t); }catch(e){}
  document.querySelectorAll('[data-theme-c]').forEach(s=>s.setAttribute('aria-pressed', s.dataset.themeC===t)); }
setTheme(localStorage.getItem('theme') || 'blue');

/* auth */
let authed = false;
async function checkAuth(){
  try{ const r=await fetch('/api/auth-status'); const a=await r.json(); authed=!!a.authenticated; }
  catch(e){ authed=false; }
  $('loginBanner').style.display = authed ? 'none' : 'block';
  applyAuthGate();
  return authed;
}
function applyAuthGate(){
  document.querySelectorAll('[data-control]').forEach(el=>{ el.disabled = !authed; });
  document.querySelectorAll('#pbtns .pbtn').forEach(b=>{ b.disabled = !authed; });
}

/* ---- profiles ---- */
const PROFILE_NOTES = {
  gaming:"Low-latency routing, split-tunnel friendly.",
  p2p:"NAT-PMP port forwarding enabled.",
  streaming:"Region-optimized exit nodes.",
  maxsec:"Strict kill switch, privacy focused."
};
let profilesCache = [];
let activeProfile = null;
async function loadProfiles(){
  try{ const r=await fetch('/api/profiles'); const j=await r.json();
    const list = Array.isArray(j) ? j : (j && j.profiles ? j.profiles : []);
    profilesCache = list.filter(p=>p.name!=='off');
  }catch(e){ profilesCache=[]; }
  renderProfiles(); populateDetailSelect();
}
function renderProfiles(){
  const wrap=$('pbtns'); wrap.innerHTML='';
  profilesCache.forEach(p=>{
    const b=document.createElement('button');
    b.className='pbtn '+(['gaming','p2p','streaming','maxsec'].includes(p.name)?p.name:'');
    b.textContent=p.label||p.name;
    if(p.color && !['gaming','p2p','streaming','maxsec'].includes(p.name)) b.style.background=p.color;
    if(p.name===activeProfile) b.classList.add('active');
    b.disabled = !authed;
    b.onclick=()=>setProfile(p.name);
    wrap.appendChild(b);
  });
}
async function setProfile(name){
  try{ const r=await fetch('/api/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile:name})});
    const j=await r.json(); if(!j.ok && j.error) toast(j.error);
  }catch(e){ toast('Switch failed'); }
  load(); loadProfiles();
}
function populateDetailSelect(){
  const sel=$('detSel'); const cur=sel.value;
  sel.innerHTML = profilesCache.map(p=>`<option value="${p.name}">${(p.label||p.name)} (${p.name}.conf)</option>`).join('');
  if(profilesCache.some(p=>p.name===cur)) sel.value=cur;
  showConf();
}
async function showConf(){
  const name=$('detSel').value; if(!name){ $('confGrid').innerHTML=''; return; }
  try{ const r=await fetch('/api/profile-conf?name='+encodeURIComponent(name)); const j=await r.json();
    if(!j.ok){ $('confGrid').innerHTML='<div class="status">'+(j.error||'Unavailable')+'</div>'; return; }
    $('confGrid').innerHTML = Object.entries(j.fields).map(([k,v])=>{
      const good=(k==='Port FWD' && String(v).startsWith('On'));
      return `<div class="heroStat"><div class="eyebrow">${k}</div><div class="v"${good?' style="color:var(--good)"':''}>${v||'\u2014'}</div></div>`;
    }).join('');
  }catch(e){ $('confGrid').innerHTML='<div class="status">Unavailable</div>'; }
}
async function deleteSelected(){
  const name=$('detSel').value; if(!name) return;
  if(!confirm('Delete profile "'+name+'"? Its .conf is moved to deleted-profiles.')) return;
  try{ const r=await fetch('/api/delete-profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile:name})});
    const j=await r.json(); toast(j.ok ? (j.message||'Deleted') : (j.error||'Delete failed'));
  }catch(e){ toast('Delete failed'); }
  load(); loadProfiles();
}

/* import */
let importColor='#ff7a1a';
document.querySelectorAll('#importPalette .swatch').forEach(sw=>{
  sw.addEventListener('click',()=>{ document.querySelectorAll('#importPalette .swatch').forEach(s=>s.setAttribute('aria-pressed','false')); sw.setAttribute('aria-pressed','true'); importColor=sw.dataset.c; });
});
async function importVpnConfig(){
  const f=$('confFile'); if(!f.files[0]){ toast('Choose a .conf file first'); return; }
  const fd=new FormData(); fd.append('config', f.files[0]); fd.append('color', importColor);
  setText('importStatus','Importing\u2026');
  try{ const r=await fetch('/api/import-config',{method:'POST',body:fd}); const j=await r.json();
    setText('importStatus', j.ok ? (j.message||'Imported') : (j.error||'Import failed'));
    if(j.ok){ f.value=''; $('fileName').textContent='No file chosen'; loadProfiles(); }
  }catch(e){ setText('importStatus','Import failed'); }
}

/* devices */
let openDevs = new Set(), lastDevJson = "", _devs = [];
function devKey(c){ return (c.mac && c.mac!=='unknown') ? c.mac : (c.ip||''); }
function toggleDev(el,key){ if(openDevs.has(key)){ openDevs.delete(key); el.classList.remove('open'); } else { openDevs.add(key); el.classList.add('open'); } }
async function renameDev(ev,key){ ev.stopPropagation();
  const c=_devs.find(d=>devKey(d)===key); if(!c) return;
  const cur=(c.name && !c.name.startsWith('Device')) ? c.name : '';
  const name=prompt('Name for '+(c.ip||c.mac)+':', cur); if(name===null) return;
  try{ const r=await fetch('/api/client-name',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mac:c.mac,ip:c.ip,name:name})});
    const j=await r.json(); toast(j.ok?'Renamed':(j.error||'Rename failed')); if(j.ok){ lastDevJson=''; load(); }
  }catch(e){ toast('Rename failed'); }
}
function renderDevices(clients){
  $('devTitle').textContent='Connected devices \u00b7 '+clients.length;
  _devs=clients;
  const j=JSON.stringify(clients); if(j===lastDevJson) return; lastDevJson=j;
  const box=$('deviceList');
  if(!clients.length){ box.innerHTML='<div class="status">No devices connected.</div>'; return; }
  box.innerHTML = clients.map(c=>{ const k=devKey(c); return `
    <div class="dev${openDevs.has(k)?' open':''}" onclick="toggleDev(this,'${k}')">
      <div class="devTop">
        <div><div class="devName">${(c.name||'Device')}</div><div class="devMeta">${c.ip||''} \u00b7 ${c.mac||''}</div></div>
        <div class="chev">&#8964;</div>
      </div>
      <div class="devBody">
        <div class="kv"><div class="k">Down</div><div class="v">${fmtBytes(c.rx)}</div></div>
        <div class="kv"><div class="k">Up</div><div class="v">${fmtBytes(c.tx)}</div></div>
        <div class="kv"><div class="k">State</div><div class="v">${c.state||'\u2014'}</div></div>
        <div class="kv"><div class="k">Policy</div><div class="v">${c.policy||'VPN'}</div></div>
        <button class="btn ghost" data-control style="grid-column:1 / -1; padding:9px; font-size:13px" onclick="renameDev(event,'${k}')">Rename device</button>
      </div>
    </div>`; }).join('');
  applyAuthGate();
}

/* wifi */
const CH={ a:[36,40,44,48,149,153,157,161,165], bg:[1,6,11] };
function populateWifiChannels(){
  const band=$('wifiBand').value, chan=$('wifiChannel'), cur=chan.value;
  if(band==='auto'){ chan.innerHTML='<option>Auto</option>'; chan.disabled=true; return; }
  chan.disabled=!authed?true:false;
  chan.innerHTML='<option>Auto</option>'+CH[band].map(c=>`<option>${c}</option>`).join('');
  if(Array.from(chan.options).some(o=>o.value===cur)) chan.value=cur;
}
let wifiLoaded=false;
async function loadWifi(){
  try{ const r=await fetch('/api/wifi?ts='+Date.now()); const j=await r.json();
    if(!j.ok){ setText('wifiStatus', j.error||'Wi-Fi unavailable'); return; }
    $('wifiSsid').value=j.ssid||'';
    $('wifiBand').value=(j.band==='a'||j.band==='bg')?j.band:'auto';
    populateWifiChannels();
    const ch=String(j.channel||'0'); if(Array.from($('wifiChannel').options).some(o=>o.value===ch)) $('wifiChannel').value=ch;
    const valid=['wpa-psk','sae','wpa-psk sae']; $('wifiSecurity').value=valid.includes(j.security)?j.security:'wpa-psk';
    $('wifiPassword').value=''; $('wifiPassword').placeholder=j.has_password?'Leave blank to keep current':'Set a password (8-63 chars)';
    $('wifiQr').src='/wifi-qr.png?ts='+Date.now();
    setText('wifiStatus','Active connection: '+(j.connection||''));
    wifiLoaded=true;
  }catch(e){ setText('wifiStatus','Wi-Fi unavailable'); }
}
async function applyWifi(){
  const body={ ssid:$('wifiSsid').value.trim(), band:$('wifiBand').value, channel:$('wifiChannel').value, security:$('wifiSecurity').value };
  const pw=$('wifiPassword').value; if(pw) body.password=pw;
  if(!confirm('Apply Wi-Fi changes? This restarts the hotspot and disconnects wireless clients.')) return;
  setText('wifiStatus','Applying\u2026');
  try{ const r=await fetch('/api/wifi',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); const j=await r.json();
    setText('wifiStatus', j.ok ? (j.message||'Applied') : (j.error||'Failed'));
    if(j.ok){ $('wifiPassword').value=''; $('wifiQr').src='/wifi-qr.png?ts='+Date.now(); setTimeout(loadWifi,4000); }
  }catch(e){ setText('wifiStatus','Failed'); }
}

/* reboot + password */
async function saveReboot(){
  try{ await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reboot_day:$('rebootDay').value, reboot_time:$('rebootTime').value})}); toast('Schedule saved'); }
  catch(e){ toast('Save failed'); }
}
async function changePassword(){
  const oldp=prompt('Current dashboard password:'); if(oldp===null) return;
  const newp=prompt('New dashboard password (min 8 characters):'); if(newp===null) return;
  try{ const r=await fetch('/api/change-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({old_password:oldp,new_password:newp})});
    const j=await r.json(); toast(j.ok ? 'Password updated' : (j.error||'Failed'));
  }catch(e){ toast('Failed'); }
}

/* power + confirm + toast */
function togglePower(e){ e.stopPropagation(); $('powerMenu').classList.toggle('show'); }
document.addEventListener('click',()=>$('powerMenu').classList.remove('show'));
const ACTIONS={
  vpnoff:{title:'Turn off VPN?',msg:'All client traffic will leave unprotected until you reconnect.',btn:'Turn off VPN',danger:false},
  hotspot:{title:'Restart hotspot?',msg:'Wireless clients will disconnect and need to rejoin.',btn:'Restart hotspot',danger:false},
  reboot:{title:'Reboot computer?',msg:'The router and dashboard go offline for roughly 30-60 seconds.',btn:'Reboot',danger:true},
  shutdown:{title:'Shutdown computer?',msg:'The router powers off. You will need to power it back on physically.',btn:'Shutdown',danger:true}
};
let pendingAction=null;
function confirmAction(kind){ $('powerMenu').classList.remove('show'); pendingAction=kind; const a=ACTIONS[kind];
  $('cfTitle').textContent=a.title; $('cfMsg').textContent=a.msg; const ok=$('cfOk'); ok.textContent=a.btn; ok.className='btn'+(a.danger?' danger':''); $('confirmModal').classList.add('show'); }
function cancelConfirm(){ $('confirmModal').classList.remove('show'); pendingAction=null; }
async function runConfirm(){ const kind=pendingAction; cancelConfirm(); if(!kind) return;
  try{ const r=await fetch('/api/power',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:kind})}); const j=await r.json(); toast(j.ok?(j.message||'Done'):(j.error||'Failed')); if(kind==='vpnoff') load(); }
  catch(e){ toast('Request failed'); } }
function toast(msg){ const t=$('toast'); t.textContent=msg; t.classList.add('show'); clearTimeout(t._t); t._t=setTimeout(()=>t.classList.remove('show'),2600); }

/* speed test */
let stRunning=false;
async function runSpeedTest(){
  if(stRunning) return; stRunning=true;
  $('stBtn').textContent='Testing\u2026'; $('stBtn').disabled=true; setText('stStatus','Running\u2026 this takes a few seconds.'); setText('stDown','\u2026'); setText('stResult','\u2026');
  try{ const r=await fetch('/api/speedtest',{method:'POST'}); const j=await r.json();
    setText('stResult', j.ok ? j.result : (j.error||'Failed'));
    const m=(j.result||'').match(/([\d.]+)\s*Mbps/i); setText('stDown', m ? m[1]+' Mb/s' : '\u2014');
    setText('stStatus','Last run just now.');
  }catch(e){ setText('stResult','Failed'); }
  $('stBtn').textContent='Run speed test'; $('stBtn').disabled=!authed; stRunning=false;
}

/* ---- live traffic graph ---- */
let rx=new Array(60).fill(0), tx=new Array(60).fill(0), hoverIndex=null;
const cv=$('graph'); const gtip=$('gtip');
function sizeCanvas(){ const r=cv.getBoundingClientRect(); cv.width=r.width*devicePixelRatio; cv.height=150*devicePixelRatio; }
function pushGraph(downBps,upBps){ rx.push(downBps*8/1e6); rx.shift(); tx.push(upBps*8/1e6); tx.shift(); draw(); }
function draw(){
  const ctx=cv.getContext('2d'), w=cv.width, h=cv.height; ctx.clearRect(0,0,w,h);
  const prim=cssVar('--primary'), good=cssVar('--good');
  const maxv=Math.max(1, Math.max.apply(null,rx), Math.max.apply(null,tx));
  const line=(arr,color,fill)=>{ ctx.beginPath(); arr.forEach((v,i)=>{ const x=i/(arr.length-1)*w, y=h-(v/maxv)*h*0.85-6; i?ctx.lineTo(x,y):ctx.moveTo(x,y); });
    ctx.lineWidth=2*devicePixelRatio; ctx.strokeStyle=color; ctx.stroke(); ctx.lineTo(w,h); ctx.lineTo(0,h); ctx.closePath();
    const g=ctx.createLinearGradient(0,0,0,h); g.addColorStop(0,fill); g.addColorStop(1,'transparent'); ctx.fillStyle=g; ctx.fill(); };
  line(rx,prim,'rgba(79,140,255,.18)'); line(tx,good,'rgba(53,208,127,.16)');
  if(hoverIndex!=null){ const gx=hoverIndex/(rx.length-1)*w;
    ctx.strokeStyle='rgba(255,255,255,.35)'; ctx.lineWidth=1*devicePixelRatio; ctx.beginPath(); ctx.moveTo(gx,0); ctx.lineTo(gx,h); ctx.stroke();
    const dot=(v,c)=>{ const y=h-(v/maxv)*h*0.85-6; ctx.beginPath(); ctx.arc(gx,y,4*devicePixelRatio,0,7); ctx.fillStyle=c; ctx.fill(); ctx.lineWidth=2*devicePixelRatio; ctx.strokeStyle='rgba(0,0,0,.5)'; ctx.stroke(); };
    dot(rx[hoverIndex],prim); dot(tx[hoverIndex],good); }
}
function gAnnotate(e){ const rect=cv.getBoundingClientRect(), x=e.clientX-rect.left; let i=Math.round(x/rect.width*(rx.length-1)); i=Math.max(0,Math.min(rx.length-1,i));
  hoverIndex=i; draw(); const ago=(rx.length-1-i)*1;
  gtip.innerHTML='<b>'+agoFmt(ago)+'</b><br>\u2193 '+rx[i].toFixed(1)+' \u00b7 \u2191 '+tx[i].toFixed(1)+' Mb/s'; gtip.style.display='block';
  gtip.style.left=Math.min(Math.max(x,34),rect.width-34)+'px'; }
cv.addEventListener('pointermove',gAnnotate); cv.addEventListener('pointerdown',gAnnotate);
cv.addEventListener('pointerleave',()=>{ if(window.matchMedia('(hover:hover)').matches){ hoverIndex=null; gtip.style.display='none'; draw(); } });

/* ---- metric modal (history from backend) ---- */
const METRICS={ 'CPU temp':{api:'temp',unit:'\u00b0C',dp:1,color:'--bad'}, 'CPU load':{api:'cpu',unit:'%',dp:0,color:'--primary'}, 'Memory':{api:'mem',unit:'%',dp:0,color:'--good'}, 'Storage':{api:'disk',unit:'%',dp:0,color:'--warn'} };
let mHist=[], mMeta=null, mHover=null;
async function openMetric(name){ mMeta=METRICS[name]; if(!mMeta) return; mHover=null; $('mtip').style.display='none'; $('mTitle').textContent=name; $('metricModal').classList.add('show');
  try{ const r=await fetch('/api/metric-history?name='+mMeta.api); const j=await r.json(); mHist=(j.ok&&j.samples&&j.samples.length)?j.samples.slice():[0]; }catch(e){ mHist=[0]; }
  drawMetric(); }
function closeMetric(){ $('metricModal').classList.remove('show'); }
function drawMetric(){ const mcv=$('mGraph'); const r=mcv.getBoundingClientRect(); mcv.width=r.width*devicePixelRatio; mcv.height=180*devicePixelRatio;
  const ctx=mcv.getContext('2d'), w=mcv.width, h=mcv.height; ctx.clearRect(0,0,w,h); const col=cssVar(mMeta.color);
  const min=Math.min.apply(null,mHist), max=Math.max.apply(null,mHist), span=(max-min)||1;
  ctx.beginPath(); mHist.forEach((v,i)=>{ const x=mHist.length>1?i/(mHist.length-1)*w:0, y=h-((v-min)/span)*h*0.78-h*0.12; i?ctx.lineTo(x,y):ctx.moveTo(x,y); });
  ctx.lineWidth=2*devicePixelRatio; ctx.strokeStyle=col; ctx.stroke(); ctx.lineTo(w,h); ctx.lineTo(0,h); ctx.closePath();
  const g=ctx.createLinearGradient(0,0,0,h); g.addColorStop(0,col+'40'); g.addColorStop(1,'transparent'); ctx.fillStyle=g; ctx.fill();
  if(mHover!=null){ const gx=mHist.length>1?mHover/(mHist.length-1)*w:0; ctx.strokeStyle='rgba(255,255,255,.35)'; ctx.lineWidth=1*devicePixelRatio; ctx.beginPath(); ctx.moveTo(gx,0); ctx.lineTo(gx,h); ctx.stroke();
    const v=mHist[mHover], y=h-((v-min)/span)*h*0.78-h*0.12; ctx.beginPath(); ctx.arc(gx,y,4*devicePixelRatio,0,7); ctx.fillStyle=col; ctx.fill(); }
  const cur=mHist[mHist.length-1]; $('mNow').innerHTML='<b>'+(cur==null?'\u2014':cur.toFixed(mMeta.dp))+'</b> '+mMeta.unit; }
(function(){ const mcv=$('mGraph'), mtip=$('mtip');
  function mAnnotate(e){ if(!mMeta||!mHist.length) return; const rect=mcv.getBoundingClientRect(), x=e.clientX-rect.left; let i=Math.round(x/rect.width*(mHist.length-1)); i=Math.max(0,Math.min(mHist.length-1,i));
    mHover=i; drawMetric(); const ago=(mHist.length-1-i)*3; mtip.innerHTML='<b>'+agoFmt(ago)+'</b><br>'+mHist[i].toFixed(mMeta.dp)+' '+mMeta.unit; mtip.style.display='block'; mtip.style.left=Math.min(Math.max(x,34),rect.width-34)+'px'; }
  mcv.addEventListener('pointermove',mAnnotate); mcv.addEventListener('pointerdown',mAnnotate);
  mcv.addEventListener('pointerleave',()=>{ if(window.matchMedia('(hover:hover)').matches){ mHover=null; mtip.style.display='none'; drawMetric(); } }); })();

window.addEventListener('keydown',e=>{ if(e.key==='Escape'){ closeMetric(); cancelConfirm(); $('powerMenu').classList.remove('show'); } });
window.addEventListener('resize',()=>{ sizeCanvas(); draw(); });

/* ---- main poll ---- */
let lastStatus=null;
async function load(){
  await checkAuth();
  let s;
  try{ const r=await fetch('/api/status'); s=await r.json(); }
  catch(e){ setText('stateText','API offline'); return; }
  const up=!!s.vpn_up;
  $('dot').classList.toggle('off', !up);
  setText('stateText', up?'Connected':'Disconnected');
  $('profChip').textContent = up ? (s.profile_label||s.profile) : 'VPN off';
  $('heroState').textContent = up ? ('Connected \u00b7 '+(s.profile_label||s.profile)) : 'Disconnected';
  activeProfile = s.profile;

  // throughput + live graph handled by pollRate() every 1s

  setText('tExitIp', up?s.current_ip:'\u2014');
  setText('tNode', up?(s.server||s.route):'\u2014');
  setText('tHandshake', s.handshake_age==null?'\u2014':agoFmt(s.handshake_age));
  setText('tUptime', s.machine?s.machine.uptime:'\u2014');
  setText('tLatency', (s.latency&&s.latency.internet_ms!=null)?(s.latency.internet_ms+' ms'):'\u2014');
  setText('tLoss', s.packet_loss==null?'\u2014':(s.packet_loss+'%'));
  const dns=$('tDns'); if(s.dns_ok==null){ dns.textContent='\u2014'; dns.style.color=''; } else { dns.textContent=s.dns_ok?'OK':'Fail'; dns.style.color=s.dns_ok?'var(--good)':'var(--bad)'; }

  const pf=$('pfVal');
  if(s.port_forwarding_supported){ const p=s.tcp_port||s.udp_port; pf.textContent = p ? ('TCP '+(s.tcp_port||'\u2014')+' \u00b7 UDP '+(s.udp_port||'\u2014')) : 'Waiting\u2026'; pf.style.color='var(--good)'; }
  else { pf.textContent='Off'; pf.style.color='var(--muted)'; }

  if(s.totals && s.totals.current_vpn){ setText('totDown', fmtBytes(s.totals.current_vpn.rx)); setText('totUp', fmtBytes(s.totals.current_vpn.tx)); }
  setText('totSession', s.profile_uptime);

  if(s.machine){
    setText('mCpu', (s.machine.cpu_percent!=null?s.machine.cpu_percent+'%':'\u2014'));
    setText('mTemp', s.machine.cpu_temp_c==null?'\u2014':(s.machine.cpu_temp_c.toFixed(1)+' \u00b0C'));
    setText('mMem', (s.machine.mem_used_percent!=null?s.machine.mem_used_percent+'%':'\u2014'));
    setText('mDisk', (s.machine.disk_used_percent!=null?s.machine.disk_used_percent+'%':'\u2014'));
  }
  renderDevices(s.clients||[]);
  setText('pnote', PROFILE_NOTES[s.profile] || ('Active profile: '+(s.profile_label||s.profile)));

  if(s.settings){ if($('rebootDay').value!==(s.settings.reboot_day||'off')) $('rebootDay').value=s.settings.reboot_day||'off'; if(document.activeElement!==$('rebootTime')) $('rebootTime').value=s.settings.reboot_time||'04:00'; }
  lastStatus=s;
}

/* live throughput + graph poll (fast, lightweight) */
let lastRate=null;
async function pollRate(){
  try{ const r=await fetch('/api/rate'); const j=await r.json();
    if(j && j.time!=null){
      if(lastRate){ const dt=Math.max(j.time-lastRate.time,0.001);
        const down=Math.max((j.rx-lastRate.rx)/dt,0), upr=Math.max((j.tx-lastRate.tx)/dt,0);
        $('rxRate').textContent=mbps(down); $('txRate').textContent=mbps(upr); pushGraph(down,upr);
      }
      lastRate=j;
    }
  }catch(e){}
}

/* init */
sizeCanvas(); draw();
loadProfiles();
load();
pollRate(); setInterval(pollRate, 1000);
setInterval(load, 3000);
</script>
</body>
</html>
"""

@app.route("/")
def index():
    if not is_authed():
        return redirect("/login")
    return render_template_string(HTML2)



def proton_port_service_active():
    try:
        r = subprocess.run(["systemctl", "is-active", "--quiet", "proton-port.service"], timeout=3)
        return r.returncode == 0
    except Exception:
        return False

def known_profile_names():
    names = []
    try:
        for fn in os.listdir(PROFILE_DIR):
            if fn.endswith(".conf"):
                n = fn[:-5]
                if re.match(r"^[A-Za-z0-9._-]+$", n):
                    names.append(n)
    except Exception:
        pass
    ordered = [x for x in CORE_PROFILES if x in names]
    ordered += sorted(x for x in names if x not in CORE_PROFILES)
    return ordered

def active_profile_supports_port_forwarding(profile):
    if not profile or profile == "off":
        return False

    if profile == "p2p":
        return True

    try:
        meta = load_profile_meta()
        if profile in meta and meta[profile].get("port_forwarding"):
            return True
    except Exception:
        pass

    path = os.path.join(PROFILE_DIR, profile + ".conf")
    try:
        text = open(path, "r", errors="ignore").read().lower()
    except Exception:
        return False

    for line in text.splitlines():
        clean = line.strip().lower()
        if not clean:
            continue

        if "nat-pmp" in clean or "port forwarding" in clean or "port-forwarding" in clean or "portforwarding" in clean:
            if any(x in clean for x in ["off", "false", "disabled", "no"]):
                continue
            return True

    return False

@app.route("/api/status")
def status():
    auth = require_control_auth()
    if auth: return auth
    wg_raw=run(["wg"],3)
    vpn_up="interface: wg0" in wg_raw
    profile=current_profile_name() or read("/run/vpn-profile-current") or "unknown"
    age_sec=port_age_seconds()
    cl=cached_value("clients", 2.5, clients)
    wg=iface_bytes("wg0")
    temp=cpu_temp()
    settings=load_settings()
    machine={"cpu_percent":cpu_percent(),"cpu_temp_c":temp,"mem_used_percent":mem_percent(),"disk_used_percent":disk_percent(),"uptime":uptime()}
    sample_metric_history(temp, machine["cpu_percent"], machine["mem_used_percent"], machine["disk_used_percent"])

    return jsonify({
        "time":time.time(),
        "profile":profile,
        "profile_label":read_profile_display(profile),
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
        "tcp_port":read("/run/proton-forwarded-tcp-port") if active_profile_supports_port_forwarding(profile) and vpn_up and proton_port_service_active() else None,
        "udp_port":read("/run/proton-forwarded-udp-port") if active_profile_supports_port_forwarding(profile) and vpn_up and proton_port_service_active() else None,
        "port_age":port_age() if active_profile_supports_port_forwarding(profile) and vpn_up and proton_port_service_active() else None,
        "port_age_warning": True if (active_profile_supports_port_forwarding(profile) and vpn_up and proton_port_service_active() and age_sec is not None and age_sec > 70) else False,
        "port_forwarding_supported": active_profile_supports_port_forwarding(profile) and vpn_up,
        "interfaces":{"eth0":iface_bytes("eth0"),"wlan0":iface_bytes("wlan0"),"wg0":wg},
        "totals":traffic_totals(wg),
        "machine":machine,
        "hotspot_clients":len(cl),
        "clients":cl,
        "internet_paused":internet_paused(),
        "latency":{"internet_ms":ping_ms("1.1.1.1"),"proton_ms":ping_ms("10.2.0.1") if vpn_up else None},
        "health": health_score(),
        "handshake_age": handshake_age_seconds() if vpn_up else None,
        "packet_loss": packet_loss_percent() if vpn_up else None,
        "dns_ok": dns_ok(),
        "throttling": throttling_status()
    })

@app.route("/api/profile",methods=["POST"])
def profile():
    auth = require_control_auth()
    if auth: return auth
    if blocked_when_locked():
        return jsonify({"ok":False,"error":"Dashboard is locked or read-only"}),403

    p=request.get_json(force=True).get("profile","").strip()

    if p != "off":
        if not re.match(r"^[A-Za-z0-9._-]+$", p):
            return jsonify({"ok":False,"error":"Invalid profile"}),400
        if not os.path.exists(os.path.join(PROFILE_DIR, p + ".conf")):
            return jsonify({"ok":False,"error":"Profile config not found"}),404

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

    if settings.get("fallback_profile") not in (known_profile_names() + ["off"]):
        return jsonify({"ok":False,"error":"Invalid fallback profile"}),400

    if settings.get("reboot_day") not in ["off","daily","sun","mon","tue","wed","thu","fri","sat"]:
        return jsonify({"ok":False,"error":"Invalid reboot day"}),400

    if not re.match(r"^\d{2}:\d{2}$", settings.get("reboot_time","04:00")):
        return jsonify({"ok":False,"error":"Invalid reboot time"}),400

    allowed_profiles = known_profile_names() + ["off"]
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




def profile_display_from_conf(text, fallback):
    skip_words = [
        "netshield", "moderate nat", "nat-pmp", "port forwarding",
        "vpn accelerator", "secure core", "protocol", "platform",
        "generated", "wireguard", "router"
    ]

    # Prefer explicit name-like comments inside the config.
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue

        if raw.startswith("#"):
            val = raw.lstrip("#").strip()
            low = val.lower()
            if not val:
                continue
            if any(w in low for w in skip_words):
                continue
            if len(val) <= 64:
                return val

        if raw.lower().startswith("name") and "=" in raw:
            val = raw.split("=", 1)[1].strip()
            if val:
                return val[:64]

    # Fallback to Endpoint host if useful.
    for line in text.splitlines():
        raw = line.strip()
        if raw.lower().startswith("endpoint") and "=" in raw:
            val = raw.split("=", 1)[1].strip().split(":")[0].strip("[]")
            if val:
                return val[:64]

    return fallback

def slugify_profile_name(name):
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9._-]+", "-", name)
    name = re.sub(r"-+", "-", name).strip("-._")
    if not name:
        name = "imported-profile"
    return name[:40]

def read_profile_display(profile):
    path = os.path.join("/etc/protonvpn-profiles", profile + ".conf")
    try:
        text = open(path, "r", errors="ignore").read()
        return profile_display_from_conf(text, profile)
    except Exception:
        return profile


PROFILE_DIR = "/etc/protonvpn-profiles"
PROFILE_META_PATH = os.path.join(PROFILE_DIR, "profile-meta.json")
CORE_PROFILES = ["gaming", "p2p", "streaming", "maxsec"]
CORE_LABELS = {
    "gaming": "Gaming",
    "p2p": "P2P",
    "streaming": "Streaming",
    "maxsec": "Max Security"
}

def clean_profile_label(value, fallback="Imported Profile"):
    value = str(value or "").replace("\\n", " ").replace("\n", " ").replace("\r", " ")
    value = re.sub(r"\s+", " ", value).strip(" #:\t")
    value = re.sub(r"(?i)^key\s+for\s+", "", value).strip()
    value = re.sub(r"(?i)^wireguard\s+config\s+for\s+", "", value).strip()
    if not value:
        value = fallback
    return value[:48]

def profile_display_from_conf(text, fallback):
    fallback = clean_profile_label(os.path.splitext(fallback or "Imported Profile")[0])

    for line in text.splitlines():
        raw = line.strip()
        low = raw.lower()

        if raw.startswith("#"):
            val = raw.lstrip("#").strip()
            vl = val.lower()

            for prefix in ["name:", "profile:", "profile name:", "server:", "server name:"]:
                if vl.startswith(prefix):
                    return clean_profile_label(val.split(":", 1)[1], fallback)

            m = re.match(r"(?i)^key\s+for\s+(.+)$", val)
            if m:
                return clean_profile_label(m.group(1), fallback)

        if low.startswith("name") and "=" in raw:
            return clean_profile_label(raw.split("=", 1)[1], fallback)

    for line in text.splitlines():
        raw = line.strip()
        if raw.lower().startswith("endpoint") and "=" in raw:
            host = raw.split("=", 1)[1].strip().split(":")[0].strip("[]")
            if host:
                return clean_profile_label(host.split(".")[0], fallback)

    return fallback

def slugify_profile_name(name):
    name = clean_profile_label(name, "imported-profile").lower()
    name = re.sub(r"[^a-z0-9._-]+", "-", name)
    name = re.sub(r"-+", "-", name).strip("-._")
    return (name or "imported-profile")[:40]

def normalize_profile_color(color):
    color = str(color or "").strip()
    if re.match(r"^#[0-9a-fA-F]{6}$", color):
        return color
    return "#4f8cff"

def load_profile_meta():
    try:
        with open(PROFILE_META_PATH, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}

def save_profile_meta(meta):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    tmp = PROFILE_META_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
    os.chown(tmp, 0, 0)
    os.chmod(tmp, 0o600)
    os.replace(tmp, PROFILE_META_PATH)

def read_profile_display(profile):
    if profile in CORE_LABELS:
        return CORE_LABELS[profile]

    meta = load_profile_meta()
    if profile in meta and meta[profile].get("label"):
        return clean_profile_label(meta[profile]["label"], profile)

    path = os.path.join(PROFILE_DIR, profile + ".conf")
    try:
        text = open(path, "r", errors="ignore").read()
        return profile_display_from_conf(text, profile)
    except Exception:
        return profile

def current_profile_name():
    try:
        return open(os.path.join(PROFILE_DIR, "current-profile"), "r").read().strip()
    except Exception:
        return ""


def profile_exists_for_activation(profile):
    profile = str(profile or "").strip()
    if profile == "off":
        return True
    if not re.match(r"^[A-Za-z0-9._-]+$", profile):
        return False
    return os.path.exists(os.path.join(PROFILE_DIR, profile + ".conf"))

@app.route("/api/profiles")
def api_profiles():
    auth = require_control_auth()
    if auth: return auth

    meta = load_profile_meta()
    active = current_profile_name()
    profiles = []

    try:
        names = []
        for fname in os.listdir(PROFILE_DIR):
            if not fname.endswith(".conf"):
                continue
            name = fname[:-5]
            if re.match(r"^[A-Za-z0-9._-]+$", name):
                names.append(name)

        ordered = [x for x in CORE_PROFILES if x in names]
        ordered += sorted([x for x in names if x not in CORE_PROFILES])

        changed = False
        for name in ordered:
            core = name in CORE_PROFILES
            if not core and name not in meta:
                meta[name] = {
                    "label": read_profile_display(name),
                    "color": "#4f8cff",
                    "imported": True
                }
                changed = True

            profiles.append({
                "name": name,
                "label": read_profile_display(name),
                "color": normalize_profile_color(meta.get(name, {}).get("color", "#4f8cff")),
                "core": core,
                "active": name == active
            })

        if changed:
            save_profile_meta(meta)

    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "profiles": []}), 500

    return jsonify({"ok": True, "profiles": profiles})

@app.route("/api/import-config", methods=["POST"])
def api_import_config():
    auth = require_control_auth()
    if auth: return auth

    uploaded = request.files.get("config")
    if not uploaded:
        return jsonify({"ok": False, "error": "No config file uploaded"}), 400

    color = normalize_profile_color(request.form.get("color", "#4f8cff"))

    raw = uploaded.read()
    if len(raw) > 30000:
        return jsonify({"ok": False, "error": "Config file is too large"}), 400

    try:
        text = raw.decode("utf-8")
    except Exception:
        return jsonify({"ok": False, "error": "Config must be a UTF-8 text file"}), 400

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"

    required = ["[Interface]", "PrivateKey", "Address", "[Peer]", "PublicKey", "Endpoint", "AllowedIPs"]
    missing = [x for x in required if x not in text]
    if missing:
        return jsonify({"ok": False, "error": "Missing required WireGuard fields: " + ", ".join(missing)}), 400

    for line in text.splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        key = clean.split("=", 1)[0].strip()
        if key in ["PreUp", "PostUp", "PreDown", "PostDown"]:
            return jsonify({"ok": False, "error": f"Rejected unsafe WireGuard directive: {key}"}), 400

    fallback = os.path.splitext(uploaded.filename or "imported-profile")[0]
    display = profile_display_from_conf(text, fallback)
    slug = slugify_profile_name(display)

    os.makedirs(PROFILE_DIR, exist_ok=True)

    base = slug
    n = 2
    while os.path.exists(os.path.join(PROFILE_DIR, slug + ".conf")):
        slug = f"{base}-{n}"
        n += 1

    dest = os.path.join(PROFILE_DIR, slug + ".conf")
    tmp = dest + ".tmp"

    text = f"# Name: {display}\n" + text

    with open(tmp, "w") as f:
        f.write(text)

    os.chown(tmp, 0, 0)
    os.chmod(tmp, 0o600)
    os.replace(tmp, dest)

    meta = load_profile_meta()
    meta[slug] = {"label": display, "color": color, "imported": True}
    save_profile_meta(meta)

    return jsonify({
        "ok": True,
        "message": f"Imported new profile: {display}",
        "profile": slug,
        "label": display,
        "color": color
    })

@app.route("/api/delete-profile", methods=["POST"])
def api_delete_profile():
    auth = require_control_auth()
    if auth: return auth

    data = request.get_json(force=True)
    profile = str(data.get("profile", "")).strip()

    if not re.match(r"^[A-Za-z0-9._-]+$", profile):
        return jsonify({"ok": False, "error": "Invalid profile name"}), 400

    if profile == "off":
        return jsonify({"ok": False, "error": "Off is not a profile file"}), 400

    path = os.path.join(PROFILE_DIR, profile + ".conf")
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": "Profile not found: " + profile}), 404

    was_active = profile == current_profile_name()
    if was_active:
        try:
            subprocess.run(["/usr/local/sbin/vpn-profile", "off"], timeout=25)
        except Exception:
            pass

    deleted_dir = os.path.join(PROFILE_DIR, "deleted-profiles")
    os.makedirs(deleted_dir, exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    deleted_path = os.path.join(deleted_dir, f"{profile}.{stamp}.conf")
    shutil.move(path, deleted_path)

    if os.path.exists(path):
        return jsonify({"ok": False, "error": "Profile file still exists after delete: " + profile}), 500

    meta = load_profile_meta()
    if profile in meta:
        del meta[profile]
        save_profile_meta(meta)

    try:
        settings = load_settings()
        if settings.get("fallback_profile") == profile:
            settings["fallback_profile"] = "off"
        order = settings.get("profile_order", [])
        if isinstance(order, list):
            settings["profile_order"] = [x for x in order if x != profile]
        save_settings(settings)
    except Exception:
        pass

    try:
        default_path = os.path.join(PROFILE_DIR, "default-profile")
        if os.path.exists(default_path) and open(default_path).read().strip() == profile:
            open(default_path, "w").write("off\n")
    except Exception:
        pass

    return jsonify({
        "ok": True,
        "message": f"Deleted profile: {profile}" + (" and turned VPN off" if was_active else ""),
        "deleted_to": os.path.basename(deleted_path)
    })

def hotspot_connection():
    """Find the NetworkManager Wi-Fi connection acting as an access point.
    Detected at runtime so no SSID or connection name is hardcoded."""
    try:
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
            text=True, timeout=5)
    except Exception:
        return None
    wireless = []
    for line in out.splitlines():
        name, sep, ctype = line.rpartition(":")
        if ctype == "802-11-wireless" and name:
            wireless.append(name.replace("\\:", ":"))
    for name in wireless:
        if nm_field(name, "802-11-wireless.mode") == "ap":
            return name
    return wireless[0] if wireless else None


def nm_field(conn, field, secrets=False):
    if not conn:
        return ""
    cmd = ["nmcli"]
    if secrets:
        cmd.append("-s")
    cmd += ["-g", field, "connection", "show", conn]
    try:
        return subprocess.check_output(cmd, text=True, timeout=5).strip()
    except Exception:
        return ""


VALID_5GHZ = [36,40,44,48,52,56,60,64,100,104,108,112,116,120,124,128,
              132,136,140,144,149,153,157,161,165]


@app.route("/api/wifi")
def api_wifi():
    auth = require_control_auth()
    if auth: return auth
    conn = hotspot_connection()
    if not conn:
        return jsonify({"ok": False, "error": "No Wi-Fi access point connection found"}), 404
    band = nm_field(conn, "802-11-wireless.band")
    return jsonify({
        "ok": True,
        "connection": conn,
        "ssid": nm_field(conn, "802-11-wireless.ssid"),
        "band": band if band in ("a", "bg") else "auto",
        "channel": nm_field(conn, "802-11-wireless.channel") or "0",
        "security": nm_field(conn, "802-11-wireless-security.key-mgmt"),
        "has_password": bool(nm_field(conn, "802-11-wireless-security.psk", secrets=True))
    })


@app.route("/api/wifi", methods=["POST"])
def api_wifi_set():
    auth = require_control_auth()
    if auth: return auth
    if blocked_when_locked():
        return jsonify({"ok": False, "error": "Dashboard is locked or read-only"}), 403
    conn = hotspot_connection()
    if not conn:
        return jsonify({"ok": False, "error": "No Wi-Fi access point connection found"}), 404

    data = request.get_json(force=True)
    ssid = str(data.get("ssid", "")).strip()
    band = str(data.get("band", "")).strip()
    channel = str(data.get("channel", "")).strip()
    password = data.get("password", None)

    if not (1 <= len(ssid) <= 32):
        return jsonify({"ok": False, "error": "SSID must be 1-32 characters"}), 400
    if band not in ("a", "bg", "auto"):
        return jsonify({"ok": False, "error": "Band must be 'a' (5GHz), 'bg' (2.4GHz), or 'auto'"}), 400

    if channel in ("", "auto", "0"):
        chan = ""          # empty clears the channel -> automatic selection
    elif re.match(r"^\d{1,3}$", channel):
        ch = int(channel)
        if band == "bg" and not (1 <= ch <= 14):
            return jsonify({"ok": False, "error": "2.4GHz channel must be 1-14"}), 400
        if band == "a" and ch not in VALID_5GHZ:
            return jsonify({"ok": False, "error": "Invalid 5GHz channel"}), 400
        chan = str(ch)
    else:
        return jsonify({"ok": False, "error": "Invalid channel"}), 400

    sec = str(data.get("security", "")).strip().lower()
    SEC_MAP = {
        "wpa-psk":     ["802-11-wireless-security.key-mgmt", "wpa-psk",
                        "802-11-wireless-security.proto", "rsn",
                        "802-11-wireless-security.pmf", "1"],
        "wpa-psk sae": ["802-11-wireless-security.key-mgmt", "wpa-psk sae",
                        "802-11-wireless-security.proto", "rsn",
                        "802-11-wireless-security.pmf", "2"],
        "sae":         ["802-11-wireless-security.key-mgmt", "sae",
                        "802-11-wireless-security.proto", "rsn",
                        "802-11-wireless-security.pmf", "3"],
    }

    if band == "auto":
        band, chan = "", ""   # clear band/channel so NetworkManager auto-selects
    mods = ["802-11-wireless.ssid", ssid,
            "802-11-wireless.band", band,
            "802-11-wireless.channel", chan]

    if sec:
        if sec not in SEC_MAP:
            return jsonify({"ok": False, "error": "Unsupported security type"}), 400
        mods += SEC_MAP[sec]

    if password is not None and password != "":
        if not (8 <= len(password) <= 63):
            return jsonify({"ok": False, "error": "Password must be 8-63 characters"}), 400
        # NM uses the psk property for both WPA2 (wpa-psk) and WPA3 (sae)
        mods += ["802-11-wireless-security.psk", password]

    try:
        r = subprocess.run(["nmcli", "connection", "modify", conn] + mods,
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return jsonify({"ok": False, "error": "Failed to apply: " + (r.stderr or r.stdout).strip()}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    try:
        subprocess.run(["nmcli", "connection", "up", conn],
                       capture_output=True, text=True, timeout=25)
    except Exception:
        pass

    return jsonify({"ok": True, "message": "Wi-Fi settings applied. Wireless clients must reconnect."})


# ---- redesign stage 1: rolling metric history ----
METRIC_HISTORY = {"temp": [], "cpu": [], "mem": [], "disk": []}
METRIC_HISTORY_MAX = 120

def sample_metric_history(temp, cpu, mem, disk):
    for k, v in (("temp", temp), ("cpu", cpu), ("mem", mem), ("disk", disk)):
        if v is None:
            continue
        try:
            buf = METRIC_HISTORY[k]
            buf.append(round(float(v), 1))
            if len(buf) > METRIC_HISTORY_MAX:
                del buf[:len(buf) - METRIC_HISTORY_MAX]
        except Exception:
            pass

@app.route("/api/metric-history")
def api_metric_history():
    auth = require_control_auth()
    if auth: return auth
    name = request.args.get("name", "")
    key = {"temp":"temp","cpu":"cpu","load":"cpu","mem":"mem","memory":"mem",
           "disk":"disk","storage":"disk"}.get(name)
    if not key:
        return jsonify({"ok": False, "error": "Unknown metric"}), 400
    return jsonify({"ok": True, "name": key, "samples": METRIC_HISTORY[key]})

# ---- redesign stage 1: tunnel health collectors ----
def handshake_age_seconds():
    out = run(["wg", "show", "wg0", "latest-handshakes"], 3)
    best = None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].isdigit():
            ts = int(parts[-1])
            if ts > 0:
                age = max(0, int(time.time()) - ts)
                best = age if best is None else min(best, age)
    return best

def packet_loss_percent(host="1.1.1.1"):
    def _get():
        out = run(["ping", "-n", "-c", "3", "-w", "2", host], 5)
        m = re.search(r"(\d+(?:\.\d+)?)% packet loss", out)
        return float(m.group(1)) if m else None
    return cached_value("loss_" + host, 30, _get)

def dns_ok():
    def _get():
        return bool(run(["getent", "hosts", "cloudflare.com"], 4).strip())
    return cached_value("dns_ok", 20, _get)

# ---- redesign stage 1: public conf fields for a profile (never secrets) ----
@app.route("/api/profile-conf")
def api_profile_conf():
    auth = require_control_auth()
    if auth: return auth
    name = request.args.get("name", "")
    if not re.match(r"^[A-Za-z0-9._-]+$", name):
        return jsonify({"ok": False, "error": "Invalid profile"}), 400
    path = os.path.join(PROFILE_DIR, name + ".conf")
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": "Profile not found"}), 404
    fields = {"Label": read_profile_display(name), "Endpoint": "", "Address": "",
              "DNS": "", "AllowedIPs": "", "Port FWD": "Off", "Peer key": ""}
    try:
        for line in open(path):
            s = line.strip(); low = s.lower()
            if "=" not in s:
                continue
            val = s.split("=", 1)[1].strip()
            if low.startswith("endpoint"):     fields["Endpoint"] = val
            elif low.startswith("address"):    fields["Address"] = val
            elif low.startswith("dns"):        fields["DNS"] = val
            elif low.startswith("allowedips"): fields["AllowedIPs"] = val
            elif low.startswith("publickey"):  fields["Peer key"] = (val[:10] + "\u2026") if val else ""
    except Exception:
        pass
    if active_profile_supports_port_forwarding(name):
        fields["Port FWD"] = "On (NAT-PMP)"
    return jsonify({"ok": True, "name": name, "fields": fields})

# ---- redesign stage 1: power actions ----
@app.route("/api/power", methods=["POST"])
def api_power():
    auth = require_control_auth()
    if auth: return auth
    if blocked_when_locked():
        return jsonify({"ok": False, "error": "Dashboard is locked or read-only"}), 403
    action = (request.get_json(force=True) or {}).get("action", "")
    if action == "vpnoff":
        run(["/usr/local/sbin/vpn-profile", "off"], 50)
        return jsonify({"ok": True, "message": "VPN turned off"})
    if action == "hotspot":
        conn = hotspot_connection()
        if not conn:
            return jsonify({"ok": False, "error": "No hotspot connection found"}), 404
        subprocess.Popen(["nmcli", "connection", "up", conn])
        return jsonify({"ok": True, "message": "Hotspot restarting"})
    if action == "reboot":
        subprocess.Popen(["systemctl", "reboot"])
        return jsonify({"ok": True, "message": "Rebooting"})
    if action == "shutdown":
        subprocess.Popen(["systemctl", "poweroff"])
        return jsonify({"ok": True, "message": "Shutting down"})
    return jsonify({"ok": False, "error": "Unknown action"}), 400

@app.route("/api/rate")
def api_rate():
    auth = require_control_auth()
    if auth: return auth
    wg = iface_bytes("wg0")
    return jsonify({"time": time.time(), "rx": wg["rx"], "tx": wg["tx"]})


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
    ssid = nm_field(hotspot_connection(), "802-11-wireless.ssid") or "ProtonPi"
    password = nm_field(hotspot_connection(), "802-11-wireless-security.psk", secrets=True) or ""
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
