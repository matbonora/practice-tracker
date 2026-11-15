# Practice Tracker

Self-hosted hobby practice tracker: timer, inbox, items, log book, milestones, stats, weekly
streaks, spaced review. FastAPI + SQLite + plain-JS PWA. REST API at `/api`, MCP server at `/mcp`.
Single user, home lab (Proxmox LXC), online-only.

## Commands

```bash
pip install -e '.[dev]'
pytest                                            # must pass before every commit
PT_TOKENS='web:dev' PT_DATA_DIR=./data practice-tracker   # http://localhost:8000, token "dev"
```

## Design rules

- **KISS / YAGNI:** single user, one container, SQLite. No multi-user, offline sync, notifications,
  gamification, metronome. Add a feature only when real use asks for it.
- **DRY:** `service.Tracker` is the one domain path. REST routes (`api.py`), MCP tools
  (`mcp_server.py`) and the PWA (which only calls the REST API) stay thin: parse, call one Tracker
  method, serialize. Validation lives in the domain (`models.py`, `service.py`), never in a route.
- **Tell, don't ask:** rules that belong to a record are methods on it (`PracticeSession.stop`,
  `Item.review`, `Item.set_status`, `Kind.check_*`). Callers tell, they do not read fields and decide.
- **Generic model:** hobby -> area -> item, session -> segments. Hobby-specific detail is data
  (`Kind` field lists, focus options, `presets/*.yaml`), never code per hobby.
- A running timer is a session with `ended_at = NULL`; only one can run.
- No deletes through MCP; structure is archived (`archived_at`, status `retired`).
- Self-update: the app never runs privileged code. It writes `update-requested` to the data dir;
  the root hook `deploy/proxmox/practice-tracker-update.sh` (systemd path unit + nightly timer)
  pulls, reinstalls, health-checks, rolls back and writes `update-status.json`. Pushes to `main`
  go live on the next update, so keep `main` deployable.

## Conventions

- Datetimes are timezone-aware UTC in Python and in the DB; JSON uses ISO strings with `Z`.
  Day/week boundaries for stats use `PT_TZ` (default Europe/Zurich); weeks start Monday.
- Domain errors: `errors.py` (`NotFound` 404, `Conflict` 409, `Invalid` 422, `Ambiguous` 409 with
  `candidates`). Name arguments (hobby/area/item) accept an id or a name; ambiguous names list candidates.
- Writes record `source`: `manual`, `ha`, `midi`, `agent`. `midi` sessions under
  `PT_MIN_AUTO_SESSION_S` are dropped only when the automation stops them.
- Frontend: no build step, no framework, no CDN. Escape all user text with `esc()`.
- Simple technical English in docs and messages. New behaviour gets a test in `tests/`.

## Layout

```
src/practice_tracker/  models, service, stats, api, mcp_server, app, presets, static/
presets/               hobby seeds loaded into an empty DB
deploy/                proxmox/ (LXC installer), pi/ (USB piano auto-timer), homeassistant/, docker
```

Design doc: "Hobby Practice Tracker – Research and Design" (Claude Docs).
