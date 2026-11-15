#!/usr/bin/env bash
# Install the piano auto-timer on a Raspberry Pi (Raspberry Pi OS Lite).
#   sudo ./install.sh <vendor_id> <product_id> <practice_url> <piano_token>
# Find the IDs with `lsusb` while the piano is switched on.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
[[ $# -eq 4 ]] || { echo "usage: sudo $0 <vendor_id> <product_id> <practice_url> <piano_token>"; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"

apt-get install -y -qq curl >/dev/null
sed -e "s/xxxx/$1/" -e "s/yyyy/$2/" "$here/90-piano.rules" > /etc/udev/rules.d/90-piano.rules
install -m 644 "$here/piano-session.service" /etc/systemd/system/piano-session.service
printf 'PRACTICE_URL=%s\nPRACTICE_TOKEN=%s\n' "$3" "$4" > /etc/practice.env
chmod 600 /etc/practice.env
systemctl daemon-reload
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --action=add
echo "Done. Switch the piano off and on; check with: journalctl -u piano-session -f"
