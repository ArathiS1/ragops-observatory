"""Alert delivery: Slack, Microsoft Teams, generic webhook, PagerDuty Events v2 and SMTP email.

Every channel can be verified end to end with ``ragops alerts test`` or
``POST /v1/projects/{project}/alert-channels/{id}/test`` before you rely on it.

Environment:
  RAGOPS_PAGERDUTY_URL     Events v2 endpoint (default https://events.pagerduty.com/v2/enqueue; EU accounts use
                           https://events.eu.pagerduty.com/v2/enqueue)
  RAGOPS_SMTP_HOST/PORT    SMTP relay (port default 587)
  RAGOPS_SMTP_USER/PASSWORD  login (optional)
  RAGOPS_SMTP_SECURITY     starttls | ssl | none   (default: starttls when a user is set, otherwise none)
  RAGOPS_SMTP_FROM         sender address (default ragops@localhost)
  RAGOPS_PUBLIC_URL        used to put a "open in RAGOps" link in alerts
"""

from __future__ import annotations

import hashlib
import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

import httpx

log = logging.getLogger("ragops.alerts")

KINDS = ("slack", "teams", "webhook", "pagerduty", "email")


@dataclass
class DeliveryResult:
    ok: bool
    kind: str
    detail: str = ""

    def __bool__(self) -> bool:  # keeps `if deliver(...)` working
        return self.ok


def format_root_causes(rc: dict[str, int]) -> str:
    total = sum(rc.values())
    if not total:
        return "no diagnosed failures in window"
    parts = [f"{k.replace('_', ' ')} {v / total:.0%}" for k, v in sorted(rc.items(), key=lambda kv: -kv[1])[:4]]
    return "Root causes: " + ", ".join(parts)


def _link(payload: dict | None) -> str | None:
    base = os.environ.get("RAGOPS_PUBLIC_URL")
    project = (payload or {}).get("project")
    if base and project:
        return f"{base.rstrip('/')}/ui/#/p/{project}"
    return None


def slack_payload(title: str, body: str, severity: str, payload: dict | None = None) -> dict:
    emoji = {"critical": ":rotating_light:", "warning": ":warning:"}.get(severity, ":information_source:")
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{title}"[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{emoji} *{severity.upper()}*\n{body}"[:2900]}},
    ]
    if (link := _link(payload)):
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Open in RAGOps"}, "url": link}]})
    # `text` is the notification / fallback string; blocks are the rich message.
    return {"text": f"{title}: {body}"[:3000], "blocks": blocks}


def teams_payload(title: str, body: str, severity: str, payload: dict | None = None) -> dict:
    card = {"type": "AdaptiveCard", "version": "1.4", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "body": [{"type": "TextBlock", "size": "Large", "weight": "Bolder", "text": title, "wrap": True,
                      "color": "Attention" if severity == "critical" else "Warning"},
                     {"type": "TextBlock", "text": body, "wrap": True}]}
    if (link := _link(payload)):
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Open in RAGOps", "url": link}]
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}


def pagerduty_payload(routing_key: str, title: str, body: str, severity: str, payload: dict | None = None) -> dict:
    p = payload or {}
    # dedup_key groups repeated firings of the same alert into one PagerDuty incident.
    dedup = hashlib.sha1(f"{p.get('project', '')}|{title}".encode()).hexdigest()[:32]
    event = {"routing_key": routing_key, "event_action": "trigger", "dedup_key": dedup,
             "payload": {"summary": title[:1024], "severity": severity if severity in ("critical", "error", "warning", "info") else "warning",
                         "source": f"ragops-observatory/{p.get('project', 'unknown')}", "component": "rag-pipeline",
                         "group": p.get("project", "ragops"), "class": "rag-quality",
                         "custom_details": {"body": body, **p}},
             "client": "RAGOps Observatory"}
    if (link := _link(payload)):
        event["client_url"] = link
        event["links"] = [{"href": link, "text": "Open in RAGOps"}]
    return event


def email_message(to: str, title: str, body: str, severity: str, payload: dict | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"[RAGOps {severity}] {title}"
    msg["To"] = to
    msg["From"] = os.environ.get("RAGOPS_SMTP_FROM", "ragops@localhost")
    link = _link(payload)
    msg.set_content(body + (f"\n\nOpen in RAGOps: {link}" if link else "") + "\n\n-- RAGOps Observatory")
    return msg


def _send_email(msg: EmailMessage) -> None:
    host = os.environ.get("RAGOPS_SMTP_HOST")
    if not host:
        raise RuntimeError("RAGOPS_SMTP_HOST not set")
    user = os.environ.get("RAGOPS_SMTP_USER")
    security = os.environ.get("RAGOPS_SMTP_SECURITY", "starttls" if user else "none").lower()
    port = int(os.environ.get("RAGOPS_SMTP_PORT", "465" if security == "ssl" else "587"))
    ctx = ssl.create_default_context()
    smtp_cls = smtplib.SMTP_SSL if security == "ssl" else smtplib.SMTP
    kwargs = {"context": ctx} if security == "ssl" else {}
    with smtp_cls(host, port, timeout=15, **kwargs) as s:
        if security == "starttls":
            s.starttls(context=ctx)
        if user:
            s.login(user, os.environ.get("RAGOPS_SMTP_PASSWORD", ""))
        s.send_message(msg)


def send(kind: str, target: str, title: str, body: str, severity: str = "warning",
         payload: dict | None = None) -> DeliveryResult:
    """Deliver one alert and report exactly what happened."""
    try:
        if kind == "slack":
            r = httpx.post(target, json=slack_payload(title, body, severity, payload), timeout=10)
        elif kind == "teams":
            r = httpx.post(target, json=teams_payload(title, body, severity, payload), timeout=10)
        elif kind == "webhook":
            r = httpx.post(target, json={"title": title, "body": body, "severity": severity, **(payload or {})}, timeout=10)
        elif kind == "pagerduty":
            url = os.environ.get("RAGOPS_PAGERDUTY_URL", "https://events.pagerduty.com/v2/enqueue")
            r = httpx.post(url, json=pagerduty_payload(target, title, body, severity, payload), timeout=10)
        elif kind == "email":
            _send_email(email_message(target, title, body, severity, payload))
            return DeliveryResult(True, kind, f"accepted by {os.environ.get('RAGOPS_SMTP_HOST')}")
        else:
            raise ValueError(f"unknown channel kind {kind!r} (use one of {', '.join(KINDS)})")
        if r.status_code >= 300:
            return DeliveryResult(False, kind, f"HTTP {r.status_code}: {r.text[:300]}")
        return DeliveryResult(True, kind, f"HTTP {r.status_code}")
    except Exception as e:  # network errors, SMTP errors, bad config
        log.warning("alert delivery to %s failed: %s", kind, e)
        return DeliveryResult(False, kind, f"{type(e).__name__}: {e}")


def deliver(kind: str, target: str, title: str, body: str, severity: str = "warning", payload: dict | None = None) -> bool:
    res = send(kind, target, title, body, severity, payload)
    if not res.ok:
        log.warning("alert delivery to %s failed: %s", kind, res.detail)
    return res.ok


def test_alert(project: str) -> tuple[str, str, dict]:
    return (f"RAGOps test alert for {project}",
            "This is a test from RAGOps Observatory. If you can read this, the channel works.",
            {"project": project, "test": True})
