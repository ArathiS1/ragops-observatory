"""SSO (OIDC) against a mock identity provider that signs real RS256 ID tokens and enforces PKCE."""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.conftest import ADMIN

ISSUER = "https://idp.example.com"


class MockIdP:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwks = {"keys": [{**jwk, "kid": "k1", "use": "sig", "alg": "RS256"}]}
        self.codes: dict[str, dict] = {}
        self.claims_override: dict = {}
        self.nonce_override = None

    def authorize(self, url: str, email="priya@acme.com", groups=("ml-engineers",)) -> str:
        """What the user's browser does at the IdP: returns the auth code for the redirect."""
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert q["code_challenge_method"] == "S256" and q["response_type"] == "code"
        code = f"code-{len(self.codes)}"
        self.codes[code] = {"challenge": q["code_challenge"], "nonce": q["nonce"], "client_id": q["client_id"],
                            "email": email, "groups": list(groups)}
        return code, q["state"]

    def handler(self, req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/authorize",
                                             "token_endpoint": f"{ISSUER}/token", "jwks_uri": f"{ISSUER}/jwks"})
        if p == "/jwks":
            return httpx.Response(200, json=self.jwks)
        if p == "/token":
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            c = self.codes.pop(form["code"], None)
            if c is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            chal = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            if chal != c["challenge"]:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PKCE mismatch"})
            now = int(time.time())
            claims = {"iss": ISSUER, "aud": c["client_id"], "sub": c["email"], "email": c["email"],
                      "email_verified": True, "name": "Priya", "groups": c["groups"], "iat": now, "exp": now + 300,
                      "nonce": self.nonce_override or c["nonce"], **self.claims_override}
            tok = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})
            return httpx.Response(200, json={"id_token": tok, "access_token": "x", "token_type": "Bearer"})
        return httpx.Response(404)


@pytest.fixture()
def idp(monkeypatch):
    from ragops import sso

    m = MockIdP()
    monkeypatch.setenv("RAGOPS_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("RAGOPS_OIDC_CLIENT_ID", "ragops")
    monkeypatch.setenv("RAGOPS_PUBLIC_URL", "https://ragops.acme.com")
    monkeypatch.setenv("RAGOPS_OIDC_ORG", "acme")
    monkeypatch.setenv("RAGOPS_OIDC_ROLE_MAP", json.dumps({"ragops-admins": "admin", "ml-engineers": "editor"}))
    monkeypatch.setenv("RAGOPS_OIDC_DEFAULT_ROLE", "none")
    monkeypatch.setenv("RAGOPS_OIDC_ALLOWED_DOMAINS", "acme.com")
    monkeypatch.setattr(sso, "_http", httpx.Client(transport=httpx.MockTransport(m.handler)))
    sso._cache.clear()
    return m


def _login(client, idp, **kw):
    r = client.get("/auth/oidc/login", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith(f"{ISSUER}/authorize?")
    code, state = idp.authorize(r.headers["location"], **kw)
    return client.get("/auth/oidc/callback", params={"code": code, "state": state}, follow_redirects=False)


def test_full_sso_login_and_org_scope(client, idp):
    client.post("/v1/orgs", json={"name": "acme"}, headers=ADMIN)
    client.post("/v1/projects", json={"name": "acme-bot", "org": "acme"}, headers=ADMIN)
    client.post("/v1/projects", json={"name": "other-bot"}, headers=ADMIN)  # default org
    assert client.get("/auth/config").json() == {"sso": True, "provider_name": "SSO"}
    r = _login(client, idp)
    assert r.status_code == 302 and r.headers["location"].startswith("https://ragops.acme.com/ui/#/sso?code=")
    handoff = r.headers["location"].split("code=")[1]
    tok = client.post("/auth/oidc/exchange", json={"code": handoff}).json()
    assert tok["token"].startswith("rgs_") and tok["role"] == "editor" and tok["org"] == "acme"
    assert client.post("/auth/oidc/exchange", json={"code": handoff}).status_code == 400  # one-time
    h = {"Authorization": f"Bearer {tok['token']}"}
    assert client.get("/auth/me", headers=h).json()["scope"] == "organisation"
    assert [p["name"] for p in client.get("/v1/projects", headers=h).json()] == ["acme-bot"]
    assert client.get("/v1/projects/other-bot/overview", headers=h).status_code == 403
    assert client.post("/v1/projects/acme-bot/slos", headers=h, json={"name": "s", "metric": "faithfulness",
                                                                       "threshold": 0.7, "target": 0.9}).status_code == 201
    assert client.get("/v1/projects/acme-bot/keys", headers=h).status_code == 403  # editor, not admin
    assert client.post("/auth/logout", headers=h).json()["signed_out"] is True
    assert client.get("/v1/projects", headers=h).status_code == 401


def test_admin_group_maps_to_admin(client, idp):
    r = _login(client, idp, groups=["ml-engineers", "ragops-admins"])
    tok = client.post("/auth/oidc/exchange", json={"code": r.headers["location"].split("code=")[1]}).json()
    assert tok["role"] == "admin"


@pytest.mark.parametrize("case,status", [
    ("no_group", 403), ("wrong_domain", 403), ("bad_nonce", 401), ("wrong_audience", 401), ("expired", 401),
    ("replayed_state", 400)])
def test_sso_rejections(client, idp, case, status):
    kw = {}
    if case == "no_group":
        kw["groups"] = ["sales"]
    if case == "wrong_domain":
        kw["email"] = "eve@evil.com"
    if case == "bad_nonce":
        idp.nonce_override = "attacker-nonce"
    if case == "wrong_audience":
        idp.claims_override = {"aud": "some-other-app"}
    if case == "expired":
        idp.claims_override = {"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200}
    if case == "replayed_state":
        r = client.get("/auth/oidc/login", follow_redirects=False)
        code, state = idp.authorize(r.headers["location"])
        assert client.get("/auth/oidc/callback", params={"code": code, "state": state},
                          follow_redirects=False).status_code == 302
        code2, _ = idp.authorize(r.headers["location"])
        r = client.get("/auth/oidc/callback", params={"code": code2, "state": state}, follow_redirects=False)
    else:
        r = _login(client, idp, **kw)
    assert r.status_code == status, r.text


def test_session_expiry(client, idp, monkeypatch):
    from ragops.db import session_scope
    from ragops.models import SsoSession, utcnow

    r = _login(client, idp)
    tok = client.post("/auth/oidc/exchange", json={"code": r.headers["location"].split("code=")[1]}).json()["token"]
    with session_scope() as db:
        for s in db.query(SsoSession).all():
            s.expires_at = utcnow()
    assert client.get("/v1/projects", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_sso_disabled_by_default(client, monkeypatch):
    monkeypatch.delenv("RAGOPS_OIDC_ISSUER", raising=False)
    assert client.get("/auth/config").json()["sso"] is False
    assert client.get("/auth/oidc/login", follow_redirects=False).status_code == 404
