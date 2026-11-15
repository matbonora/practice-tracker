import json

import pytest
from fastapi.testclient import TestClient

from practice_tracker.app import create_app

AUTH = {"Authorization": "Bearer test-token"}
MCP_HEADERS = {"Authorization": "Bearer agent-token", "Accept": "application/json, text/event-stream",
               "Content-Type": "application/json"}


@pytest.fixture
def client(settings, clock):
    app = create_app(settings, clock)
    with TestClient(app) as c:
        c.clock = clock
        yield c


def test_auth_required(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/summary").status_code == 401
    assert client.get("/api/summary", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/").status_code == 200  # PWA shell is public


def test_timer_flow_and_assign(client):
    r = client.post("/api/timer/start", json={"hobby": "Piano"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["running"]
    assert client.post("/api/timer/start", json={}, headers=AUTH).status_code == 409
    client.clock.advance(minutes=30)
    client.post("/api/timer/split", headers=AUTH)
    r = client.post("/api/timer/toggle", headers=AUTH)
    sid = r.json()["session"]["id"]
    assert r.json()["outcome"] == "inbox"
    assert len(client.get("/api/inbox", headers=AUTH).json()) == 1
    r = client.put(f"/api/sessions/{sid}/segments", headers=AUTH, json={
        "segments": [{"item": "D minor", "minutes": 10, "focus": ["arpeggios"], "params": {"bpm": 72}},
                     {"area": "Pieces", "minutes": 15, "next_focus": "bars 13-20 slow"}],
        "rating": 4})
    assert r.status_code == 200, r.text
    assert r.json()["unassigned_min"] == 5
    assert client.get("/api/inbox", headers=AUTH).json() == []


def test_ambiguous_name_gives_candidates(client):
    r = client.post("/api/timer/start", json={"item": "minor"}, headers=AUTH)
    assert r.status_code == 409
    assert len(r.json()["candidates"]) == 12


def test_structure_and_validation(client):
    r = client.post("/api/areas", headers=AUTH, json={"hobby": "Piano", "name": "Improvisation", "target_share": 0.1})
    assert r.status_code == 201
    r = client.post("/api/items", headers=AUTH, json={"hobby": "Piano", "area": "Pieces", "title": "Gymnopédie 1",
                                                      "attributes": {"composer": "Satie", "target_bpm": 70}})
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    bad = client.patch(f"/api/items/{iid}", headers=AUTH, json={"attributes": {"tempo": 3}})
    assert bad.status_code == 422
    r = client.patch(f"/api/items/{iid}", headers=AUTH, json={"status": "repertoire"})
    assert r.json()["review_due"] is not None
    assert client.post(f"/api/items/{iid}/review", headers=AUTH, json={"rating": 3}).status_code == 200


def test_summary_stats_export_and_upload(client):
    client.post("/api/sessions", headers=AUTH, json={"hobby": "Piano", "segments": [
        {"area": "Pieces", "minutes": 20}, {"area": "Sight reading", "minutes": 10}]})
    s = client.get("/api/summary", headers=AUTH).json()
    assert s["today_min"] == 30 and s["running"] is False
    rows = client.get("/api/stats/minutes?group=area&period=day", headers=AUTH).json()
    assert {r["name"] for r in rows} == {"Pieces", "Sight reading"}
    assert client.get("/api/stats/streak?hobby=Piano", headers=AUTH).json()["this_week_min"] == 30
    assert "Pieces" in client.get("/api/export.csv", headers=AUTH).text
    assert len(client.get("/api/export.json", headers=AUTH).json()["segments"]) == 2
    m = client.post("/api/milestones", headers=AUTH, json={"title": "First recital", "hobby": "Piano"}).json()
    up = client.post("/api/attachments", headers=AUTH, data={"owner_type": "milestone", "owner_id": m["id"]},
                     files={"file": ("take1.m4a", b"fake-audio", "audio/mp4")})
    assert up.status_code == 201, up.text
    assert client.get(f"/api/attachments/{up.json()['id']}/file", headers=AUTH).content == b"fake-audio"


def _mcp(client, method, params, id_=1):
    r = client.post("/mcp", headers=MCP_HEADERS, follow_redirects=False,
                    json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params})
    assert r.status_code == 200, r.text
    return r.json()


def test_mcp_requires_token(client):
    r = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert r.status_code == 401


def test_mcp_tools(client):
    init = _mcp(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "test", "version": "1"}})
    assert init["result"]["serverInfo"]["name"] == "practice-tracker"
    tools = {t["name"] for t in _mcp(client, "tools/list", {})["result"]["tools"]}
    assert {"get_overview", "log_session", "upsert_item", "timer", "get_stats"} <= tools

    def call(name, args):
        res = _mcp(client, "tools/call", {"name": name, "arguments": args})["result"]
        return res

    res = call("log_session", {"hobby": "Piano", "segments": [
        {"item": "C major", "minutes": 8, "focus": ["scale runs"], "params": {"bpm": 90}}]})
    assert not res.get("isError"), res
    payload = res.get("structuredContent") or json.loads(res["content"][0]["text"])
    assert payload["source"] == "agent"

    res = call("upsert_item", {"title": "Clair de lune", "area": "Pieces", "hobby": "Piano",
                               "attributes": {"composer": "Debussy"}})
    assert not res.get("isError"), res
    res = call("upsert_item", {"title": "Clair de lune", "status": "polishing"})
    assert not res.get("isError"), res
    detail = call("get_item", {"item": "Clair de lune"})
    body = detail.get("structuredContent") or json.loads(detail["content"][0]["text"])
    assert body["status"] == "polishing"

    err = call("timer", {"action": "start", "item": "minor"})
    assert err["isError"] and "Candidates" in err["content"][0]["text"]
