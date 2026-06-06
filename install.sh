#!/bin/bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "Run with sudo: sudo ./install.sh"
  exit 1
fi

echo "Installing ProtonPi Router..."

apt update
apt install -y \
  wireguard \
  resolvconf \
  iptables-persistent \
  netfilter-persistent \
  python3-flask \
  python3-werkzeug \
  qrencode \
  natpmpc \
  openssl \
  curl \
  dnsmasq \
  iproute2 \
  network-manager

mkdir -p /opt/vpn-dashboard
mkdir -p /etc/protonvpn-profiles
mkdir -p /etc/protonpi-dashboard/certs
mkdir -p /var/lib/protonpi-dashboard/backups

cp dashboard/app.py /opt/vpn-dashboard/app.py
cp scripts/* /usr/local/sbin/

chmod +x /usr/local/sbin/vpn-profile
chmod +x /usr/local/sbin/proton-port
chmod +x /usr/local/sbin/vpn-watchdog
chmod +x /usr/local/sbin/protonpi-backup
chmod +x /usr/local/sbin/protonpi-restore
chmod +x /usr/local/sbin/protonpi-health
chmod +x /usr/local/sbin/protonpi-device-control
chmod +x /usr/local/sbin/protonpi-apply-limits
chmod +x /usr/local/sbin/protonpi-set-password

cp systemd/*.service /etc/systemd/system/
cp systemd/*.timer /etc/systemd/system/

chmod 700 /etc/protonvpn-profiles
chmod 700 /etc/protonpi-dashboard/certs
chmod 700 /var/lib/protonpi-dashboard
chmod 700 /var/lib/protonpi-dashboard/backups

systemctl daemon-reload
systemctl enable vpn-dashboard-https.service
systemctl enable vpn-dashboard-redirect.service
systemctl enable vpn-watchdog.timer
systemctl enable vpn-autostart.service

echo
echo "Install complete."
echo
echo "Next steps:"
echo "1. Add Proton WireGuard configs to /etc/protonvpn-profiles/"
echo "2. Run: sudo protonpi-set-password 'your-password'"
echo "3. Start services:"
echo "   sudo systemctl start vpn-dashboard-https.service"
echo "   sudo systemctl start vpn-dashboard-redirect.service"
echo "   sudo systemctl start vpn-watchdog.timer"
