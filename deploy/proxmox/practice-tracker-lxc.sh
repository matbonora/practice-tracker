#!/usr/bin/env bash
# Practice Tracker on Proxmox VE: create a Debian LXC and install the app, or update it.
#
# Run on the Proxmox host as root:
#   ./practice-tracker-lxc.sh install            # new container, settings from env vars below
#   ./practice-tracker-lxc.sh update <CTID>      # new code into an existing container
#
# Update hook: the container gets /usr/local/bin/practice-tracker-update. It pulls the git repo
# (PT_REPO_URL in /etc/practice-tracker.env), reinstalls, restarts and rolls back if the health
# check fails. It runs every night (systemd timer; AUTO_UPDATE=0 to disable), from the app's
# "Update now" button (Settings) and by hand:
#   pct exec <CTID> -- practice-tracker-update
#
# One-liner (no checkout needed, clones the public repo inside the container):
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/matbonora/practice-tracker/main/deploy/proxmox/practice-tracker-lxc.sh)" -- install
#
# Code source: the checkout this script lives in (packed and pushed with pct push) or, when run
# without a checkout or with REPO_URL set, a git clone inside the container.
#
# Settings (env vars, all optional):
#   CTID (next free id)  CT_HOSTNAME=practice  STORAGE=local-lvm  TEMPLATE_STORAGE=local
#   BRIDGE=vmbr0  IP=dhcp (or 192.168.1.50/24,gw=192.168.1.1)  CORES=1  MEMORY=512  DISK=4
#   TZ_NAME=Europe/Zurich  REPO_URL=https://github.com/matbonora/practice-tracker  REPO_REF=main
#   PORT=8000  AUTO_UPDATE=1  UPDATE_TIME=04:00
set -euo pipefail

CT_HOSTNAME=${CT_HOSTNAME:-practice}
STORAGE=${STORAGE:-local-lvm}
TEMPLATE_STORAGE=${TEMPLATE_STORAGE:-local}
BRIDGE=${BRIDGE:-vmbr0}
IP=${IP:-dhcp}
CORES=${CORES:-1}
MEMORY=${MEMORY:-512}
DISK=${DISK:-4}
TZ_NAME=${TZ_NAME:-Europe/Zurich}
REPO_URL=${REPO_URL:-}
REPO_REF=${REPO_REF:-main}
PORT=${PORT:-8000}
AUTO_UPDATE=${AUTO_UPDATE:-1}
UPDATE_TIME=${UPDATE_TIME:-04:00}
APP_DIR=/opt/practice-tracker
DATA_DIR=/var/lib/practice-tracker
DEFAULT_REPO=https://github.com/matbonora/practice-tracker
SCRIPT_PATH=${BASH_SOURCE[0]:-}
SRC_DIR=""
if [[ -n $SCRIPT_PATH && -f $SCRIPT_PATH ]]; then
  SRC_DIR="$(cd "$(dirname "$SCRIPT_PATH")/../.." && pwd)"
fi
# no local checkout (e.g. run via curl): clone from GitHub inside the container
if [[ -z $REPO_URL && ! -f ${SRC_DIR:-/nonexistent}/pyproject.toml ]]; then
  REPO_URL=$DEFAULT_REPO
fi

msg() { printf '\e[1;34m==>\e[0m %s\n' "$*"; }
die() { printf '\e[1;31mERROR:\e[0m %s\n' "$*" >&2; exit 1; }
in_ct() { pct exec "$CTID" -- bash -c "$1"; }

require_host() {
  [[ $EUID -eq 0 ]] || die "run as root on the Proxmox host"
  command -v pct >/dev/null || die "pct not found - is this a Proxmox VE host?"
}

pick_template() {
  pveam update >/dev/null
  local t
  for series in debian-13-standard debian-12-standard; do
    t=$(pveam available --section system | awk -v s="$series" '$2 ~ s {print $2}' | sort -V | tail -1)
    [[ -n $t ]] && break
  done
  [[ -n $t ]] || die "no Debian template found (pveam available)"
  if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$t"; then
    msg "Downloading template $t"
    pveam download "$TEMPLATE_STORAGE" "$t" >/dev/null
  fi
  echo "$TEMPLATE_STORAGE:vztmpl/$t"
}

wait_for_network() {
  msg "Waiting for network in CT $CTID"
  for _ in $(seq 1 30); do
    if in_ct "getent hosts deb.debian.org >/dev/null 2>&1"; then return 0; fi
    sleep 2
  done
  die "container has no network/DNS after 60 s"
}

push_code() {
  if [[ -n $REPO_URL ]]; then
    msg "Fetching $REPO_URL ($REPO_REF)"
    in_ct "if [ -d $APP_DIR/.git ]; then git -C $APP_DIR fetch -q origin && git -C $APP_DIR checkout -q $REPO_REF && git -C $APP_DIR reset -q --hard origin/$REPO_REF; else rm -rf $APP_DIR && git clone -q --branch $REPO_REF $REPO_URL $APP_DIR; fi"
  else
    msg "Pushing code from $SRC_DIR"
    local tarball
    tarball=$(mktemp /tmp/practice-tracker.XXXX.tar.gz)
    tar -C "$SRC_DIR" --exclude=.git --exclude=data --exclude='__pycache__' --exclude=.venv -czf "$tarball" .
    pct push "$CTID" "$tarball" /tmp/practice-tracker.tar.gz
    rm -f "$tarball"
    in_ct "mkdir -p $APP_DIR && find $APP_DIR -mindepth 1 -maxdepth 1 ! -name .venv -exec rm -rf {} + && tar -C $APP_DIR -xzf /tmp/practice-tracker.tar.gz && rm /tmp/practice-tracker.tar.gz"
  fi
}

install_python_env() {
  msg "Installing the app"
  in_ct "
    set -e
    if python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
      [ -d $APP_DIR/.venv ] || python3 -m venv $APP_DIR/.venv
    else
      # Debian 12 ships Python 3.11: use uv to get a newer interpreter
      command -v uv >/dev/null || (curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh)
      [ -d $APP_DIR/.venv ] || uv venv --python 3.13 $APP_DIR/.venv
      $APP_DIR/.venv/bin/python -m ensurepip -q 2>/dev/null || true
    fi
    $APP_DIR/.venv/bin/pip install -q --upgrade pip
    $APP_DIR/.venv/bin/pip install -q $APP_DIR
    chown -R practice:practice $DATA_DIR
  "
}

install_hook() {
  local repo=${REPO_URL:-$DEFAULT_REPO}
  msg "Installing update hook (auto update: $([[ $AUTO_UPDATE == 1 ]] && echo "daily $UPDATE_TIME" || echo off))"
  in_ct "grep -q '^PT_REPO_URL=' /etc/practice-tracker.env || { echo 'PT_REPO_URL=$repo'; echo 'PT_REPO_REF=$REPO_REF'; } >> /etc/practice-tracker.env"
  in_ct "install -m 755 $APP_DIR/deploy/proxmox/practice-tracker-update.sh /usr/local/bin/practice-tracker-update
cat > /etc/systemd/system/practice-tracker-update.service <<'UNIT'
[Unit]
Description=Practice Tracker update (git pull, reinstall, restart, rollback on failure)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/practice-tracker-update
UNIT
cat > /etc/systemd/system/practice-tracker-update.timer <<'UNIT'
[Unit]
Description=Nightly Practice Tracker update

[Timer]
OnCalendar=*-*-* $UPDATE_TIME
RandomizedDelaySec=30min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
cat > /etc/systemd/system/practice-tracker-update.path <<'UNIT'
[Unit]
Description=Run the Practice Tracker update when the app requests it

[Path]
PathExists=$DATA_DIR/update-requested
Unit=practice-tracker-update.service

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now practice-tracker-update.path >/dev/null 2>&1"
  if [[ $AUTO_UPDATE == 1 ]]; then
    in_ct "systemctl enable --now practice-tracker-update.timer >/dev/null 2>&1"
  else
    in_ct "systemctl disable --now practice-tracker-update.timer >/dev/null 2>&1 || true"
  fi
}

cmd_install() {
  require_host
  CTID=${CTID:-$(pvesh get /cluster/nextid)}
  pct status "$CTID" >/dev/null 2>&1 && die "CT $CTID already exists (use: $0 update $CTID)"
  local template
  template=$(pick_template)

  msg "Creating CT $CTID ($CT_HOSTNAME) from $template"
  pct create "$CTID" "$template" \
    --hostname "$CT_HOSTNAME" --cores "$CORES" --memory "$MEMORY" --swap 256 \
    --rootfs "$STORAGE:$DISK" --net0 "name=eth0,bridge=$BRIDGE,ip=$IP" \
    --unprivileged 1 --features nesting=1 --onboot 1 --timezone "$TZ_NAME" \
    --description "Practice Tracker - http://<ip>:$PORT" >/dev/null
  pct start "$CTID"
  wait_for_network

  msg "Installing packages"
  in_ct "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq python3 python3-venv git curl ca-certificates >/dev/null"
  in_ct "id practice >/dev/null 2>&1 || useradd -r -d $DATA_DIR -s /usr/sbin/nologin practice; mkdir -p $DATA_DIR"

  push_code
  install_python_env

  msg "Writing config and service"
  local t_web t_ha t_piano t_agent
  t_web=$(openssl rand -hex 24); t_ha=$(openssl rand -hex 24); t_piano=$(openssl rand -hex 24); t_agent=$(openssl rand -hex 24)
  in_ct "cat > /etc/practice-tracker.env <<EOF
PT_DATA_DIR=$DATA_DIR
PT_PRESETS_DIR=$APP_DIR/presets
PT_TZ=$TZ_NAME
PT_PORT=$PORT
PT_TOKENS=web:$t_web,ha:$t_ha,piano:$t_piano,agent:$t_agent
PT_REPO_URL=${REPO_URL:-$DEFAULT_REPO}
PT_REPO_REF=$REPO_REF
EOF
chmod 600 /etc/practice-tracker.env"
  in_ct "cat > /etc/systemd/system/practice-tracker.service <<'EOF'
[Unit]
Description=Practice Tracker
After=network-online.target
Wants=network-online.target

[Service]
User=practice
Group=practice
EnvironmentFile=/etc/practice-tracker.env
ExecStart=$APP_DIR/.venv/bin/practice-tracker
Restart=on-failure
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=$DATA_DIR
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable --now practice-tracker"
  install_hook

  local ip
  ip=$(in_ct "hostname -I | awk '{print \$1}'")
  sleep 3
  in_ct "curl -fsS http://localhost:$PORT/api/health >/dev/null" || die "service did not start: pct exec $CTID -- journalctl -u practice-tracker"
  cat <<EOF

Practice Tracker is running in CT $CTID: http://$ip:$PORT

Tokens (also in /etc/practice-tracker.env inside the CT; keep them secret):
  web   (PWA)             $t_web
  ha    (Home Assistant)  $t_ha
  piano (Pi at the piano) $t_piano
  agent (MCP clients)     $t_agent

MCP endpoint: http://$ip:$PORT/mcp   API docs: http://$ip:$PORT/api/docs
Backups: include CT $CTID in your vzdump schedule (data lives in $DATA_DIR).
Updates: nightly from ${REPO_URL:-$DEFAULT_REPO} ($REPO_REF); now: pct exec $CTID -- practice-tracker-update
Note: phones need HTTPS for the PWA install prompt - put it behind your reverse proxy.
EOF
}

cmd_update() {
  require_host
  CTID=${1:-${CTID:-}}
  [[ -n $CTID ]] || die "usage: $0 update <CTID>"
  pct status "$CTID" | grep -q running || die "CT $CTID is not running"
  if [[ -n $REPO_URL ]] && in_ct "test -x /usr/local/bin/practice-tracker-update"; then
    # normal case: the hook in the container pulls, reinstalls and rolls back on failure
    in_ct "practice-tracker-update"
    install_hook  # refresh timer settings (AUTO_UPDATE, UPDATE_TIME)
    return
  fi
  # local checkout, or an older install without the hook
  push_code
  install_python_env
  install_hook
  in_ct "systemctl restart practice-tracker"
  sleep 2
  in_ct "curl -fsS http://localhost:$PORT/api/health >/dev/null" && msg "Updated and running"
}

case "${1:-}" in
  install) cmd_install ;;
  update) shift; cmd_update "$@" ;;
  *) echo "usage: $0 install | update <CTID>"; exit 1 ;;
esac
