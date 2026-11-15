# Practice Tracker

Self-hosted practice log for hobbies (piano, drawing, anything). One timer you start once and assign
later, areas and items per hobby, a log book, milestones with photos/recordings, weekly-goal streaks,
statistics, spaced review for repertoire, a REST API (Home Assistant) and an MCP server (agents).

Python 3.12+, FastAPI, SQLite, plain-JS PWA. No build step.

## Run

**Proxmox (recommended):** on the Proxmox host as root:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/matbonora/practice-tracker/main/deploy/proxmox/practice-tracker-lxc.sh)" -- install
# later, to update CT 123 to the latest main:
bash -c "$(curl -fsSL https://raw.githubusercontent.com/matbonora/practice-tracker/main/deploy/proxmox/practice-tracker-lxc.sh)" -- update 123
```

It creates a Debian LXC, installs the app from GitHub and prints the URL and tokens. From a local
checkout, `./deploy/proxmox/practice-tracker-lxc.sh install` pushes that checkout instead.

**Updates:** the container has an update hook, `practice-tracker-update`. It pulls `main`, reinstalls,
restarts and rolls back if the health check fails. It runs nightly (`AUTO_UPDATE=0` at install to
turn off), from **Settings → Update now** in the app, or by hand: `pct exec <CTID> -- practice-tracker-update`.
The app only writes a request file (`update-requested` in the data dir); a systemd path unit starts
the hook as root, and the hook reports to `update-status.json`. Logs: `journalctl -u practice-tracker-update`.

Settings via env vars (`CTID`, `STORAGE`, `BRIDGE`, `IP`, `MEMORY`, `REPO_URL`, ...), see the script header.
With `REPO_URL` set, the container clones from git instead of receiving this checkout.

**Docker:** `cd deploy && docker compose up -d` (set the tokens in `docker-compose.yml` first).

**Local / dev:**

```bash
pip install -e '.[dev]'
PT_TOKENS='web:dev' PT_DATA_DIR=./data practice-tracker   # http://localhost:8000, token "dev"
pytest
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `PT_TOKENS` | (required) | `name:token,...`; names: `web`, `ha`, `piano`, `agent` (any names work) |
| `PT_DATA_DIR` | `./data` | SQLite file `app.db` and `media/` |
| `PT_DB_URL` | SQLite in data dir | Any SQLAlchemy URL, e.g. PostgreSQL |
| `PT_TZ` | `Europe/Zurich` | Day and week boundaries for statistics |
| `PT_PORT` / `PT_HOST` | `8000` / `0.0.0.0` | Listen address |
| `PT_FORGOTTEN_HOURS` | `3` | Running timer flagged after this |
| `PT_MIN_AUTO_SESSION_S` | `120` | Auto-stopped MIDI sessions shorter than this are dropped |
| `PT_PRESETS_DIR` | `./presets` | YAML presets loaded into an empty database |

Backup: the data directory (or the whole LXC with vzdump).

## Use

- **PWA:** open the URL, enter the `web` token (gear icon). On the phone use "Add to home screen";
  the install prompt needs HTTPS, so put it behind your reverse proxy.
- **Timer:** start (optionally with area/item), "Next block" marks a split, stop. Without an item the
  session lands in the **Inbox**; assigning pre-fills one block per split.
- **Structure:** hobbies, kinds, areas and items come from `presets/*.yaml` on first start and are
  editable through the API/MCP; items also in the app.
- **Repertoire review:** set a piece's status to `repertoire`; due reviews appear on the Timer tab
  (1 again, 2 hard, 3 good, 4 easy: next interval ×1, ×1.2, ×2, ×3, max 60 days).

## Integrations

- **Home Assistant:** `deploy/homeassistant/practice.yaml` (toggle/split commands, summary sensors).
- **Piano auto-timer (Raspberry Pi + USB MIDI):** `deploy/pi/`. Piano on = session runs.
  `sudo ./install.sh <vendor> <product> http://practice.lan:8000 <piano token>`. Check first with
  `udevadm monitor --udev --subsystem-match=usb` that the piano disappears from USB when switched off.
- **REST API:** docs at `/api/docs`. Auth: `Authorization: Bearer <token>`.
- **MCP:** streamable HTTP at `/mcp`, same bearer auth (use the `agent` token). Tools: `get_overview`,
  `timer`, `log_session`, `list_inbox`, `assign_session`, `recent_sessions`, `upsert_hobby`,
  `upsert_kind`, `upsert_area`, `upsert_item`, `get_item`, `review_item`, `add_log_entry`,
  `add_milestone`, `get_stats`. No delete tools; writes are tagged `source: agent`.

  Claude Code: `claude mcp add --transport http practice http://practice.lan:8000/mcp --header "Authorization: Bearer <agent token>"`

## Layout

```
src/practice_tracker/
  models.py      tables + their rules (timer stop, review interval, field validation)
  service.py     Tracker: the one domain service behind REST, MCP and UI
  stats.py       minutes per period/group, area split, weekly streaks, summary
  api.py         REST routes (thin)
  mcp_server.py  MCP tools (thin)
  app.py         wiring, bearer auth, static PWA
  static/        PWA (index.html, app.js, app.css, sw.js)
presets/         hobby seeds (piano, drawing)
deploy/          proxmox/, pi/, homeassistant/, docker-compose.yml
```
