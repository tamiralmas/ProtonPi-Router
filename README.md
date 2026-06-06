# ProtonPi Router

ProtonPi Router turns a Raspberry Pi into a Proton VPN Wi-Fi router with a mobile-friendly HTTPS dashboard.

## Features

- Proton VPN WireGuard profile switching
- Gaming, P2P, streaming, and max-security profiles
- Wi-Fi hotspot routing through Proton VPN
- VPN kill switch
- P2P NAT-PMP port forwarding support
- HTTPS dashboard at `https://protonpi.local`
- Login and trusted-device access
- Live traffic graphs and system statistics
- Connected-device controls
- Backup and restore tools
- VPN watchdog and fallback profile support

## Hardware

Tested on:

- Raspberry Pi 4 Model B
- Raspberry Pi OS Lite 64-bit
- Proton VPN Pro

## Security Warning

Do not commit real Proton VPN configs, private keys, dashboard auth files, certificates, or backup archives.

Keep these private:

- `/etc/protonvpn-profiles/*.conf`
- `/etc/wireguard/wg0.conf`
- `/etc/protonvpn-profiles/dashboard-auth.json`
- `/etc/protonpi-dashboard/certs/protonpi.key`
- `/var/lib/protonpi-dashboard/backups/*.tar.gz`

## Install

```bash
sudo ./install.sh
