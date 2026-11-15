import json
import os
import time

from fastapi.testclient import TestClient

from practice_tracker.app import create_app

AUTH = {"Authorization": "Bearer test-token"}


def test_update_request_and_status(settings, clock):
    with TestClient(create_app(settings, clock)) as c:
        s = c.get("/api/system/update", headers=AUTH).json()
        assert s == {"hook_installed": False, "requested": False, "stale_request": False, "last": None}

        r = c.post("/api/system/update", headers=AUTH, json={"force": True})
        assert r.status_code == 202 and r.json()["requested"]
        assert (settings.data_dir / "update-requested").read_text().strip() == "force"

        # nobody picks it up for a while -> stale
        old = time.time() - 300
        os.utime(settings.data_dir / "update-requested", (old, old))
        assert c.get("/api/system/update", headers=AUTH).json()["stale_request"]

        # hook consumed the request and is running
        (settings.data_dir / "update-requested").unlink()
        (settings.data_dir / "update-status.json").write_text(json.dumps(
            {"state": "running", "message": "updating", "commit": "abc", "ref": "main", "time": "x"}))
        s = c.get("/api/system/update", headers=AUTH).json()
        assert s["hook_installed"] and s["last"]["state"] == "running"
        assert c.post("/api/system/update", headers=AUTH, json={}).status_code == 409

        assert c.post("/api/system/update", json={}).status_code == 401
