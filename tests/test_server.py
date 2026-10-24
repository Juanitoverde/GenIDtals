import base64
import socket
import threading
import time

import cv2
import pytest

pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")
from fastapi.testclient import TestClient  # noqa: E402

from genidtals import ConfigurationError, generate_key  # noqa: E402
from genidtals.client import GenIDtalsAPIError, GenIDtalsClient  # noqa: E402
from genidtals.server import create_app, openapi_schema  # noqa: E402

API_KEY = "test-key-0123456789abcdefghijklmnop"


def b64(img):
    return base64.b64encode(cv2.imencode(".png", img)[1].tobytes()).decode()


@pytest.fixture
def client(engine):
    return TestClient(create_app(engine, [API_KEY], max_body_bytes=8 * 1024 * 1024))


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {API_KEY}"}


def test_health_is_public(client):
    r = client.get("/v1/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong-key-wrong-key-wrong"},
                                     {"X-API-Key": "nope"}])
def test_auth_required(client, headers):
    r = client.get("/v1/info", headers=headers)
    assert r.status_code == 401 and r.json()["error"] == "unauthorized"


def test_x_api_key_header_accepted(client):
    assert client.get("/v1/info", headers={"X-API-Key": API_KEY}).status_code == 200


def test_enroll_verify_delete_flow(client, auth, specimens, captures):
    imgs = captures(specimens[1], 3)
    r = client.post("/v1/users/alice%40example.com/enroll", headers=auth,
                    json={"images": [b64(i) for i in imgs[:2]]})
    assert r.status_code == 200, r.text
    assert r.json()["references"] == 2

    r = client.post("/v1/users/alice%40example.com/verify", headers=auth, json={"image": b64(imgs[2])})
    body = r.json()
    assert r.status_code == 200 and body["authenticated"] is True and body["reason"] == "ok"
    assert body["confidence"] is None and body["modality_scores"] is None

    r = client.post("/v1/users/alice%40example.com/verify", headers=auth,
                    json={"image": b64(captures(specimens[2], 1)[0])})
    assert r.json()["authenticated"] is False and r.json()["reason"] == "no_match"

    status = client.get("/v1/users/alice%40example.com", headers=auth).json()
    assert status["enrolled"] is True and status["locked"] is False
    assert client.delete("/v1/users/alice%40example.com", headers=auth).json()["deleted"] is True


def test_error_mapping(client, auth, specimens, captures):
    imgs = [b64(i) for i in captures(specimens[1], 2)]
    client.post("/v1/users/bob/enroll", headers=auth, json={"images": imgs})
    r = client.post("/v1/users/bob/enroll", headers=auth, json={"images": imgs})
    assert r.status_code == 409 and r.json()["error"] == "already_enrolled"

    r = client.post("/v1/users/bob/verify", headers=auth, json={"image": "%%%not-base64"})
    assert r.status_code == 422 and r.json()["error"] == "invalid_base64"

    junk = base64.b64encode(b"definitely not an image").decode()
    r = client.post("/v1/users/bob/verify", headers=auth, json={"image": junk})
    assert r.status_code == 422 and r.json()["error"] == "image"

    r = client.post("/v1/templates/verify", headers=auth,
                    json={"subject_id": "bob", "template": junk, "image": imgs[0]})
    assert r.status_code == 400 and r.json()["error"] == "invalid_template"


def test_body_limit(engine, auth):
    small = TestClient(create_app(engine, [API_KEY], max_body_bytes=1024))
    r = small.post("/v1/quality", headers=auth, json={"image": "A" * 4096})
    assert r.status_code == 413


def test_stateless_templates(client, auth, specimens, captures):
    imgs = captures(specimens[3], 3)
    r = client.post("/v1/templates", headers=auth,
                    json={"subject_id": "u1", "images": [b64(i) for i in imgs[:2]]})
    template = r.json()["template"]
    ok = client.post("/v1/templates/verify", headers=auth,
                     json={"subject_id": "u1", "template": template, "image": b64(imgs[2])})
    assert ok.json()["authenticated"] is True
    wrong = client.post("/v1/templates/verify", headers=auth,
                        json={"subject_id": "u2", "template": template, "image": b64(imgs[2])})
    assert wrong.status_code == 400


def test_quality_endpoint(client, auth, specimens, captures):
    r = client.post("/v1/quality", headers=auth, json={"image": b64(captures(specimens[1], 1)[0])})
    assert r.status_code == 200 and r.json()["score"] >= 90


def test_docs_disabled_by_default(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_refuses_insecure_configuration(engine):
    with pytest.raises(ConfigurationError):
        create_app(engine, [])
    with pytest.raises(ConfigurationError):
        create_app(engine, ["too-short"])


def test_openapi_document():
    schema = openapi_schema()
    assert "/v1/users/{user_id}/verify" in schema["paths"]
    assert schema["components"]["securitySchemes"]["bearer"]["scheme"] == "bearer"


# ------------------------------------------------- client against live server


@pytest.fixture
def live_server(engine):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(engine, [API_KEY]), host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_python_client_end_to_end(live_server, specimens, captures, tmp_path):
    imgs = captures(specimens[4], 3)
    paths = []
    for i, img in enumerate(imgs):
        p = tmp_path / f"{i}.png"
        cv2.imwrite(str(p), img)
        paths.append(p)
    c = GenIDtalsClient(live_server, API_KEY)
    assert c.health()["status"] == "ok"
    assert c.enroll("carol/1", paths[:2])["references"] == 2
    assert c.verify("carol/1", paths[2])["authenticated"] is True
    assert c.status("carol/1")["enrolled"] is True
    template = c.create_template("carol-stateless", paths[:2])
    assert c.verify_template("carol-stateless", template, paths[2].read_bytes())["authenticated"] is True
    assert c.delete_user("carol/1")["deleted"] is True
    with pytest.raises(GenIDtalsAPIError) as e:
        GenIDtalsClient(live_server, "wrong-key").info()
    assert e.value.status == 401


def test_client_url_safety():
    with pytest.raises(ValueError):
        GenIDtalsClient("file://localhost/etc/passwd", "k")
    with pytest.raises(ValueError):
        GenIDtalsClient("http://biometrics.example.com", "k")
    GenIDtalsClient("http://biometrics.internal", "k", allow_insecure=True)
    GenIDtalsClient("https://biometrics.example.com", generate_key().hex())
