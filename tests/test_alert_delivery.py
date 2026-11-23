"""Alert delivery for real: a local SMTP server receives the e-mail, a local HTTP server receives Slack/Teams/
PagerDuty/webhook calls. Payloads are checked against each provider's documented contract."""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from typer.testing import CliRunner


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def receiver():
    got: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("content-length", 0))
            got.append({"path": self.path, "json": json.loads(self.rfile.read(n) or b"{}")})
            code = 400 if self.path == "/broken" else (202 if self.path.startswith("/pd") else 200)
            self.send_response(code)
            self.end_headers()
            self.wfile.write(b'{"status":"success"}' if code < 300 else b"invalid_payload")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", got
    srv.shutdown()


@pytest.fixture()
def smtp_server(monkeypatch):
    pytest.importorskip("aiosmtpd")
    from aiosmtpd.controller import Controller
    from aiosmtpd.handlers import Message

    inbox = []

    class Box(Message):
        def handle_message(self, message):
            inbox.append(message)

    port = _free_port()
    ctl = Controller(Box(), hostname="127.0.0.1", port=port)
    ctl.start()
    monkeypatch.setenv("RAGOPS_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("RAGOPS_SMTP_PORT", str(port))
    monkeypatch.setenv("RAGOPS_SMTP_SECURITY", "none")
    monkeypatch.setenv("RAGOPS_SMTP_FROM", "alerts@ragops.test")
    yield inbox
    ctl.stop()


def test_email_is_really_delivered(smtp_server, monkeypatch):
    monkeypatch.setenv("RAGOPS_PUBLIC_URL", "https://ragops.acme.dev")
    from ragops.alerts import send

    res = send("email", "oncall@acme.dev", "SLO 'faithfulness' breached", "faithfulness >= 0.7 on 81% of 200 samples",
               "critical", {"project": "support-bot"})
    assert res.ok, res.detail
    for _ in range(50):
        if smtp_server:
            break
        time.sleep(0.05)
    m = smtp_server[0]
    assert m["To"] == "oncall@acme.dev" and m["From"] == "alerts@ragops.test"
    assert m["Subject"] == "[RAGOps critical] SLO 'faithfulness' breached"
    body = m.get_payload(decode=True).decode()
    assert "81% of 200 samples" in body and "https://ragops.acme.dev/ui/#/p/support-bot" in body


def test_email_failure_is_reported(monkeypatch):
    monkeypatch.setenv("RAGOPS_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("RAGOPS_SMTP_PORT", str(_free_port()))  # nothing listening
    from ragops.alerts import send

    res = send("email", "x@y.z", "t", "b")
    assert not res.ok and "ConnectionRefused" in res.detail


def test_slack_teams_webhook_contracts(receiver):
    url, got = receiver
    from ragops.alerts import send

    assert send("slack", url + "/slack", "Quality dropped", "Root causes: retrieval miss 60%", "warning").ok
    assert send("teams", url + "/teams", "Quality dropped", "body", "critical").ok
    assert send("webhook", url + "/hook", "Quality dropped", "body", "warning", {"project": "p1"}).ok
    slack, teams, hook = (g["json"] for g in got)
    # Slack incoming webhook: `text` fallback + Block Kit blocks
    assert slack["text"].startswith("Quality dropped") and slack["blocks"][0]["type"] == "header"
    assert slack["blocks"][1]["text"]["type"] == "mrkdwn"
    # Teams workflow webhook: message with an Adaptive Card attachment
    att = teams["attachments"][0]
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive" and att["content"]["type"] == "AdaptiveCard"
    assert hook == {"title": "Quality dropped", "body": "body", "severity": "warning", "project": "p1"}


def test_pagerduty_events_v2_contract(receiver, monkeypatch):
    url, got = receiver
    monkeypatch.setenv("RAGOPS_PAGERDUTY_URL", url + "/pd/v2/enqueue")
    from ragops.alerts import send

    a = send("pagerduty", "R0UTINGKEY0000000000000000000000", "SLO breached", "details", "critical", {"project": "p1"})
    b = send("pagerduty", "R0UTINGKEY0000000000000000000000", "SLO breached", "details again", "critical", {"project": "p1"})
    assert a.ok and b.ok and a.detail == "HTTP 202"
    e1, e2 = got[0]["json"], got[1]["json"]
    assert e1["routing_key"] == "R0UTINGKEY0000000000000000000000" and e1["event_action"] == "trigger"
    assert set(e1["payload"]) >= {"summary", "severity", "source"} and e1["payload"]["severity"] == "critical"
    assert e1["dedup_key"] == e2["dedup_key"]  # repeated firings collapse into one incident


def test_receiver_errors_surface(receiver):
    url, _ = receiver
    from ragops.alerts import send

    res = send("slack", url + "/broken", "t", "b")
    assert not res.ok and "HTTP 400" in res.detail and "invalid_payload" in res.detail


def test_channel_test_endpoint_and_cli(client, project, receiver):
    url, got = receiver
    name, h = project
    cid = client.post(f"/v1/projects/{name}/alert-channels", json={"kind": "slack", "target": url + "/slack"}, headers=h).json()["id"]
    r = client.post(f"/v1/projects/{name}/alert-channels/{cid}/test", headers=h).json()
    assert r == {"ok": True, "kind": "slack", "detail": "HTTP 200"}
    assert "test alert" in got[-1]["json"]["text"]
    assert client.delete(f"/v1/projects/{name}/alert-channels/{cid}", headers=h).status_code == 200
    assert client.post(f"/v1/projects/{name}/alert-channels/{cid}/test", headers=h).status_code == 404

    from ragops.cli import app

    out = CliRunner().invoke(app, ["alerts", "test", "--kind", "webhook", "--target", url + "/hook"])
    assert out.exit_code == 0 and "delivered via webhook: HTTP 200" in out.output
    bad = CliRunner().invoke(app, ["alerts", "test", "--kind", "webhook", "--target", url + "/broken"])
    assert bad.exit_code == 1 and "FAILED" in bad.output
