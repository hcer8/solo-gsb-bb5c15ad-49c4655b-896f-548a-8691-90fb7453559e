"""API-level tests using FastAPI's TestClient."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BODY = "--abc\r\nContent-Type: text/plain\r\n\r\nhello\r\n--abc--\r\n"


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_parse_api():
    r = client.post("/api/parse", json={
        "contentType": "multipart/mixed; boundary=abc",
        "boundary": "",
        "body": BODY,
        "mode": "body",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"]
    assert data["tree"]["parts"][0]["body"] == "hello"


def test_parse_api_bad_mode():
    r = client.post("/api/parse", json={
        "contentType": "multipart/mixed; boundary=abc",
        "body": BODY,
        "mode": "bogus",
    })
    assert r.status_code == 422


def test_encode_api_roundtrip():
    parse = client.post("/api/parse", json={
        "contentType": "multipart/mixed; boundary=abc",
        "body": BODY,
        "mode": "body",
    }).json()
    assert parse["ok"]
    tree = parse["tree"]
    tree["parts"][0]["body"] = "changed"
    r = client.post("/api/encode", json={"tree": tree, "messageHeaders": None})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"]
    assert "changed" in data["message"]
    assert data["message"].endswith("--abc--\r\n")
    assert data["bytes"] == len(data["message"].encode("utf-8"))


def test_encode_api_rejects_bad_tree():
    r = client.post("/api/encode", json={"tree": {"parts": []}, "messageHeaders": None})
    assert r.status_code == 400


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "MIME Multipart" in r.text
