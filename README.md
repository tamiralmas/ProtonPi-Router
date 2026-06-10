# ProtonPi Router

Turn a Raspberry Pi into a self-hosted Proton VPN Wi-Fi router with a live HTTPS dashboard. ProtonPi routes all hotspot traffic through a WireGuard VPN tunnel, lets you switch server profiles in one tap, and gives you a real-time view of traffic, devices, and system health — all from a mobile-friendly web interface on your local network.

---

## Features

**VPN routing**
- Full WireGuard tunnel routing through Proton VPN for all connected devices
- One-tap profile switching: Gaming, P2P, Streaming, Max Security, or any custom profile imported from a `.conf`
- Kill switch — if the tunnel drops, traffic is blocked, not leaked
- NAT-PMP port forwarding for P2P clients (automatically reads the assigned TCP/UDP port)
- VPN watchdog with automatic reconnect and fallback profile on tunnel failure

**Live dashboard**
- Served at `https://protonpi.local` over self-signed TLS
- Command bar: live connection state, active profile, real-time ↓/↑ throughput
- **Overview tab**: tunnel health (exit IP, server node, WireGuard handshake age, latency, packet loss, DNS check, port-forwarding state), cumulative session traffic, 1-second live traffic graph, CPU temp, CPU load, memory, and storage
- **Profiles tab**: dynamic profile buttons, per-profile WireGuard conf details, one-click import of custom `.conf` files with color selection, safe delete
- **Devices tab**: connected hotspot clients with per-device traffic totals and policy
- **Network tab**: Wi-Fi SSID, password, band (2.4 GHz / 5 GHz / auto), channel, and WPA2/WPA3 security — all configurable from the dashboard; scan-to-join QR code updates live
- Rolling history graphs for CPU temp, CPU load, and memory (last ~5 minutes, tap any tile)
- Speed test through the active tunnel

**Access control**
- Dashboard login with hashed password; trusted-device session tokens
- All control endpoints require authentication; read-only visitors see live status with controls disabled
- Dashboard lock mode for display-only use

**Hotspot and routing**
- Wi-Fi AP via NetworkManager (SSID `ProtonPi`, 5 GHz band a, channel 36 by default — configurable from the dashboard)
- Subnet `10.42.0.0/24`, gateway `10.42.0.1`
- Per-device traffic policies (VPN, direct, or blocked)
- Scheduled automatic reboot (configurable day and time)

**System**
- Backup and restore of profiles and settings
- Automated `protonpi-health` check script
- Separate systemd units for the dashboard, HTTP→HTTPS redirect, VPN watchdog timer, and VPN autostart

---

## Hardware

Tested on:

- Raspberry Pi 4 Model B (2 GB or 4 GB RAM)
- Raspberry Pi OS Lite 64-bit (Bookworm)
- Proton VPN Pro subscription (WireGuard config export required)
- USB Wi-Fi adapter with AP mode support, or built-in Pi Wi-Fi

---

## Install

```bash
git clone https://github.com/tamiralmas/ProtonPi-Router.git
cd ProtonPi-Router
sudo ./install.sh
```

The installer sets up dependencies (WireGuard, Flask, NetworkManager, qrencode, natpmpc, iptables-persistent), copies the dashboard and scripts into place, and enables all systemd units.

**After install:**

1. Add your Proton VPN WireGuard configs to `/etc/protonvpn-profiles/` — one `.conf` per server profile.
2. Set the dashboard password:
   ```bash
   sudo protonpi-set-password 'your-password'
   ```
3. Start the services:
   ```bash
   sudo systemctl start vpn-dashboard-https.service
   sudo systemctl start vpn-dashboard-redirect.service
   sudo systemctl start vpn-watchdog.timer
   ```
4. Open `https://protonpi.local` on any device connected to the ProtonPi hotspot.

---

## Project layout

```
dashboard/app.py          Flask dashboard (single-file)
scripts/vpn-profile       Profile switch and kill-switch management
scripts/proton-port       NAT-PMP port forwarding poller
scripts/vpn-watchdog      Tunnel health monitor and reconnect
scripts/protonpi-*        Backup, restore, health, device control, limits
systemd/                  Service and timer unit files
examples/                 Example JSON config files
install.sh                Automated installer
```

---

## Security

- Dashboard runs over HTTPS with a self-signed certificate generated at install time.
- All WireGuard private keys and preshared keys stay in `/etc/protonvpn-profiles/` and `/etc/wireguard/`, which are `chmod 700`. The dashboard never exposes them.
- The profile details view shows only public conf fields (endpoint, allowed IPs, DNS, port-forwarding support).
- Do not commit secrets to the repo — the `.gitignore` covers all `.conf`, `.key`, `.crt`, `.pem`, auth JSON, and backup archive files.

Files to keep private on the Pi (already gitignored):

```
/etc/protonvpn-profiles/*.conf
/etc/wireguard/wg0.conf
/opt/vpn-dashboard/dashboard-auth.json
/etc/protonpi-dashboard/certs/protonpi.key
/var/lib/protonpi-dashboard/backups/*.tar.gz
```

---

## Dependencies

Installed automatically by `install.sh`:

`wireguard` `resolvconf` `iptables-persistent` `python3-flask` `python3-werkzeug` `qrencode` `natpmpc` `openssl` `curl` `dnsmasq` `iproute2` `network-manager`
