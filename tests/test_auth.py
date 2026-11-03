from __future__ import annotations

import pytest

from graph_mail import auth
from graph_mail.errors import GraphMailError


def test_device_code_login_polls_until_the_browser_approves(monkeypatch):
    start = {
        "device_code": "dev-123",
        "user_code": "ABCD1234",
        "verification_uri": "https://microsoft.com/devicelogin",
        "expires_in": 900,
        "interval": 1,
    }
    responses = [
        (400, {"error": "authorization_pending"}),
        (400, {"error": "slow_down"}),
        (200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}),
    ]
    calls = []

    def fake_post(url, form):
        calls.append((url, form))
        if url.endswith("/devicecode"):
            return 200, start
        return responses.pop(0)

    saved = {}
    monkeypatch.setattr(auth, "_post_form_raw", fake_post)
    monkeypatch.setattr(auth, "save_tokens", lambda tokens: saved.update(tokens))

    messages = []
    record = auth.login_device_code(timeout=30, echo=messages.append, sleep=lambda s: None)

    assert record["access_token"] == "at"
    assert saved["refresh_token"] == "rt"
    assert any("ABCD1234" in m for m in messages)
    assert calls[1][1]["grant_type"] == auth.DEVICE_CODE_GRANT
    assert calls[1][1]["device_code"] == "dev-123"


def test_device_code_login_reports_a_declined_sign_in(monkeypatch):
    def fake_post(url, form):
        if url.endswith("/devicecode"):
            return 200, {"device_code": "dev-1", "user_code": "X", "expires_in": 900, "interval": 1}
        return 400, {"error": "authorization_declined", "error_description": "User declined"}

    monkeypatch.setattr(auth, "_post_form_raw", fake_post)

    with pytest.raises(GraphMailError) as excinfo:
        auth.login_device_code(timeout=30, echo=lambda m: None, sleep=lambda s: None)

    assert excinfo.value.code == "AUTH_FAILED"
    assert "declined" in excinfo.value.message.lower()


def test_device_code_login_times_out(monkeypatch):
    def fake_post(url, form):
        if url.endswith("/devicecode"):
            return 200, {"device_code": "dev-1", "user_code": "X", "expires_in": 0, "interval": 1}
        return 400, {"error": "authorization_pending"}

    monkeypatch.setattr(auth, "_post_form_raw", fake_post)

    with pytest.raises(GraphMailError) as excinfo:
        auth.login_device_code(timeout=0, echo=lambda m: None, sleep=lambda s: None)

    assert "timed out" in excinfo.value.message.lower()
