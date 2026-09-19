"""Real API smoke. Run inside the bootstrap container; emits no credentials/content.

docker compose run --rm --no-deps --entrypoint .venv/bin/python bootstrap - < scripts/smoke-api.py
"""

import http.cookiejar
import json
import os
import urllib.error
import urllib.request
import uuid

base = "http://api:8000"
jar = http.cookiejar.CookieJar()
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def request(method, path, payload=None, expected=200):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        base + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "X-Requested-With": "aimeet"},
    )
    try:
        response = client.open(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    assert response.status == expected, f"Unexpected status {response.status} for {method} {path}"
    data = response.read()
    return json.loads(data) if data else None


request("GET", "/api/health/ready")
request("GET", "/api/v1/auth/me", expected=401)
request(
    "POST",
    "/api/v1/auth/login",
    {
        "email": os.environ["BOOTSTRAP_EMAIL"],
        "password": os.environ["BOOTSTRAP_PASSWORD"],
    },
)
request("GET", "/api/v1/auth/me")
meeting_id = None
try:
    title = "Smoke " + uuid.uuid4().hex
    created = request(
        "POST",
        "/api/v1/meetings",
        {
            "title": title,
            "language": "ru",
            "transcript": "Локальная проверка сохранения исходного текста.",
        },
        expected=201,
    )
    meeting_id = created["id"]
    assert created["status"] == "draft"
    detail = request("GET", f"/api/v1/meetings/{meeting_id}")
    assert detail["title"] == title
    result = request("GET", "/api/v1/meetings?q=" + urllib.parse.quote(title))
    assert result["total"] == 1
finally:
    if meeting_id:
        request("DELETE", f"/api/v1/meetings/{meeting_id}", expected=204)
request("GET", f"/api/v1/meetings/{meeting_id}", expected=404)
request("POST", "/api/v1/auth/logout", expected=204)
request("GET", "/api/v1/auth/me", expected=401)
print("PASS: readiness, unauthorized access, login, session, create, read, search, delete, logout.")
