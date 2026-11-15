#!/usr/bin/env bash
# Update hook, runs inside the container as root (installed as /usr/local/bin/practice-tracker-update).
# Pulls PT_REPO_URL / PT_REPO_REF, reinstalls, restarts, checks health, rolls back on failure.
#   practice-tracker-update            # update if there are new commits
#   practice-tracker-update --force    # reinstall even if up to date
# Started by: the nightly timer, the app's "Update now" button (via a request file and a systemd
# path unit) or by hand. Progress goes to $PT_DATA_DIR/update-status.json for the app to show.
# Logs: journalctl -u practice-tracker-update
set -euo pipefail

# shellcheck disable=SC1091
source /etc/practice-tracker.env
APP_DIR=${PT_APP_DIR:-/opt/practice-tracker}
DATA_DIR=${PT_DATA_DIR:-/var/lib/practice-tracker}
REPO=${PT_REPO_URL:?PT_REPO_URL missing in /etc/practice-tracker.env}
REF=${PT_REPO_REF:-main}
PORT=${PT_PORT:-8000}
STATUS_FILE=$DATA_DIR/update-status.json
FORCE=${1:-}

REQUEST=$DATA_DIR/update-requested  # written by the app, starts us through the path unit
if [[ -f $REQUEST ]]; then
  grep -q force "$REQUEST" && FORCE=--force
  rm -f "$REQUEST"
fi

log() { echo "[practice-tracker-update] $*"; }

status() {  # status <state> <message> ; state: running | ok | up_to_date | failed | rolled_back
  local commit
  commit=$(git -C "$APP_DIR" rev-parse -q --verify HEAD 2>/dev/null || echo "")
  printf '{"state": "%s", "message": "%s", "commit": "%s", "ref": "%s", "time": "%s"}\n' \
    "$1" "$2" "${commit:0:12}" "$REF" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATUS_FILE.tmp"
  chown practice:practice "$STATUS_FILE.tmp" 2>/dev/null || true
  mv "$STATUS_FILE.tmp" "$STATUS_FILE"
  log "$1: $2"
}

healthy() {
  for _ in $(seq 1 15); do
    curl -fsS "http://localhost:$PORT/api/health" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

deploy() {  # deploy <commit>
  git -C "$APP_DIR" reset -q --hard "$1"
  "$APP_DIR/.venv/bin/pip" install -q --upgrade "$APP_DIR"
  "$APP_DIR/.venv/bin/pip" install -q --force-reinstall --no-deps "$APP_DIR"
  chown -R practice:practice "$DATA_DIR"
  systemctl restart practice-tracker
}

trap 'status failed "update script error, see journalctl -u practice-tracker-update"' ERR

cd "$APP_DIR"
if [[ ! -d .git ]]; then  # installed from a pushed checkout: turn it into a git working copy
  git init -q
  git remote add origin "$REPO"
fi
git remote set-url origin "$REPO"
status running "checking $REF for new commits"
git fetch -q --depth 50 origin "$REF"

old=$(git rev-parse -q --verify HEAD || echo none)
new=$(git rev-parse FETCH_HEAD)
if [[ $old == "$new" && $FORCE != --force ]]; then
  status up_to_date "already on the latest commit ${new:0:7}"
  exit 0
fi

status running "updating ${old:0:7} -> ${new:0:7}"
deploy "$new"
if healthy; then
  # the hook updates itself with the code it just deployed
  hook_src="$APP_DIR/deploy/proxmox/practice-tracker-update.sh"
  if [[ -f $hook_src ]]; then install -m 755 "$hook_src" /usr/local/bin/practice-tracker-update; fi
  status ok "updated to ${new:0:7}"
  exit 0
fi

trap - ERR
if [[ $old == none ]]; then
  status failed "new version ${new:0:7} is not healthy and there is nothing to roll back to"
  exit 1
fi
log "health check failed, rolling back to ${old:0:7}"
deploy "$old"
if healthy; then
  status rolled_back "version ${new:0:7} failed the health check; back on ${old:0:7}"
else
  status failed "rollback to ${old:0:7} is unhealthy too, see journalctl -u practice-tracker"
fi
exit 1
