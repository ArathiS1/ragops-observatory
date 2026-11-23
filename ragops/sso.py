"""Single sign-on with OpenID Connect (Okta, Microsoft Entra ID, Google Workspace, Auth0, Keycloak, ...).

Flow: authorization code + PKCE (S256). The ID token is verified against the provider's JWKS (signature, issuer,
audience, expiry, nonce). Users land in one organisation with a role mapped from a claim (usually ``groups``). After
the callback the browser receives a one-time code in the URL fragment and exchanges it for a session token, so the
token itself never appears in a URL, a server log or browser history.

Configuration (environment):

    RAGOPS_OIDC_ISSUER          https://acme.okta.com            (required to enable SSO)
    RAGOPS_OIDC_CLIENT_ID       ragops                           (required)
    RAGOPS_OIDC_CLIENT_SECRET   ...                              (optional: public clients use PKCE only)
    RAGOPS_PUBLIC_URL           https://ragops.acme.com          (callback = {PUBLIC_URL}/auth/oidc/callback)
    RAGOPS_OIDC_ORG             acme                             organisation SSO users belong to (default "default")
    RAGOPS_OIDC_ROLE_CLAIM      groups
    RAGOPS_OIDC_ROLE_MAP        {"ragops-admins": "admin", "ml-engineers": "editor"}
    RAGOPS_OIDC_DEFAULT_ROLE    viewer                           ("none" = deny users without a mapped group)
    RAGOPS_OIDC_ALLOWED_DOMAINS acme.com,acme.co.uk              (optional e-mail domain allowlist)
    RAGOPS_OIDC_SESSION_HOURS   12
    RAGOPS_OIDC_PROVIDER_NAME   Okta                             (label on the sign-in button)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ragops.models import OidcLoginState, SsoSession, utcnow
from ragops.security import ROLE_RANK, hash_key

STATE_TTL = timedelta(minutes=10)
HANDOFF_TTL = timedelta(minutes=2)
_http = httpx.Client(timeout=10)  # replaced in tests with a mock identity provider
_cache: dict[str, tuple[float, dict]] = {}


class SsoError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str | None
    redirect_uri: str
    org: str
    role_claim: str
    role_map: dict[str, str]
    default_role: str | None
    allowed_domains: list[str]
    session_hours: float
    provider_name: str
    scopes: str


def config() -> OidcConfig | None:
    issuer = os.environ.get("RAGOPS_OIDC_ISSUER")
    client_id = os.environ.get("RAGOPS_OIDC_CLIENT_ID")
    if not issuer or not client_id:
        return None
    public = os.environ.get("RAGOPS_PUBLIC_URL", "http://localhost:8000").rstrip("/")
    default_role = os.environ.get("RAGOPS_OIDC_DEFAULT_ROLE", "viewer")
    role_map = json.loads(os.environ.get("RAGOPS_OIDC_ROLE_MAP", "{}") or "{}")
    bad = [r for r in [*role_map.values(), default_role] if r not in (*ROLE_RANK, "none")]
    if bad:
        raise SsoError(f"unknown role(s) in SSO configuration: {bad}")
    return OidcConfig(
        issuer=issuer.rstrip("/"), client_id=client_id, client_secret=os.environ.get("RAGOPS_OIDC_CLIENT_SECRET"),
        redirect_uri=os.environ.get("RAGOPS_OIDC_REDIRECT_URL", f"{public}/auth/oidc/callback"),
        org=os.environ.get("RAGOPS_OIDC_ORG", "default"), role_claim=os.environ.get("RAGOPS_OIDC_ROLE_CLAIM", "groups"),
        role_map=role_map, default_role=None if default_role == "none" else default_role,
        allowed_domains=[d.strip().lower() for d in os.environ.get("RAGOPS_OIDC_ALLOWED_DOMAINS", "").split(",")
                         if d.strip()],
        session_hours=float(os.environ.get("RAGOPS_OIDC_SESSION_HOURS", "12")),
        provider_name=os.environ.get("RAGOPS_OIDC_PROVIDER_NAME", "SSO"),
        scopes=os.environ.get("RAGOPS_OIDC_SCOPES", "openid email profile"),
    )


def _get_json(url: str, ttl: float = 3600) -> dict:
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    r = _http.get(url)
    if r.status_code != 200:
        raise SsoError(f"identity provider returned HTTP {r.status_code} for {url}", 502)
    data = r.json()
    _cache[url] = (time.time(), data)
    return data


def discovery(cfg: OidcConfig) -> dict:
    d = _get_json(f"{cfg.issuer}/.well-known/openid-configuration")
    if d.get("issuer", "").rstrip("/") != cfg.issuer:
        raise SsoError("issuer in the discovery document does not match RAGOPS_OIDC_ISSUER", 502)
    return d


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def login_url(db: Session, return_to: str | None = None) -> str:
    cfg = config()
    if cfg is None:
        raise SsoError("SSO is not configured", 404)
    d = discovery(cfg)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    db.execute(delete(OidcLoginState).where(OidcLoginState.created_at < utcnow() - STATE_TTL))
    db.add(OidcLoginState(state=state, nonce=nonce, code_verifier=verifier, return_to=return_to))
    params = {"response_type": "code", "client_id": cfg.client_id, "redirect_uri": cfg.redirect_uri,
              "scope": cfg.scopes, "state": state, "nonce": nonce, "code_challenge_method": "S256",
              "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest())}
    return f"{d['authorization_endpoint']}?{urlencode(params)}"


def _verify_id_token(cfg: OidcConfig, d: dict, id_token: str, nonce: str) -> dict:
    import jwt

    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as e:
        raise SsoError(f"malformed ID token: {e}", 401) from e
    jwks = _get_json(d["jwks_uri"], ttl=900)
    keys = [k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")] or jwks.get("keys", [])[:1]
    if not keys:
        raise SsoError("no signing key in the provider's JWKS", 502)
    alg = header.get("alg", "RS256")
    if alg not in ("RS256", "RS384", "RS512", "ES256", "ES384", "PS256"):
        raise SsoError(f"unsupported ID token algorithm {alg}", 401)
    try:
        claims = jwt.decode(id_token, jwt.PyJWK(keys[0]).key, algorithms=[alg], audience=cfg.client_id,
                            issuer=d["issuer"], leeway=60, options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    except jwt.PyJWTError as e:
        raise SsoError(f"ID token rejected: {e}", 401) from e
    if claims.get("nonce") != nonce:
        raise SsoError("ID token nonce mismatch (possible replay)", 401)
    return claims


def map_role(cfg: OidcConfig, claims: dict) -> str | None:
    raw = claims.get(cfg.role_claim) or []
    groups = raw if isinstance(raw, list) else [raw]
    best = None
    for g in groups:
        r = cfg.role_map.get(str(g))
        if r and (best is None or ROLE_RANK[r] > ROLE_RANK[best]):
            best = r
    return best or cfg.default_role


def callback(db: Session, code: str, state: str) -> str:
    """Finish the login. Returns the URL to send the browser to (the UI, with a one-time handoff code)."""
    from ragops import services as svc

    cfg = config()
    if cfg is None:
        raise SsoError("SSO is not configured", 404)
    st = db.get(OidcLoginState, state)
    if st is None or st.created_at < utcnow() - STATE_TTL or st.nonce == "handoff":
        raise SsoError("login expired or already used; start again", 400)
    nonce, verifier, return_to = st.nonce, st.code_verifier, st.return_to
    db.delete(st)
    d = discovery(cfg)
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": cfg.redirect_uri,
            "client_id": cfg.client_id, "code_verifier": verifier}
    auth = (cfg.client_id, cfg.client_secret) if cfg.client_secret else None
    r = _http.post(d["token_endpoint"], data=data, auth=auth, headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise SsoError(f"token exchange failed (HTTP {r.status_code}): {r.text[:200]}", 401)
    claims = _verify_id_token(cfg, d, r.json().get("id_token", ""), nonce)
    email = (claims.get("email") or claims.get("preferred_username") or claims["sub"]).lower()
    if claims.get("email_verified") is False:
        raise SsoError("the identity provider says this e-mail address is not verified", 403)
    if cfg.allowed_domains and email.rsplit("@", 1)[-1] not in cfg.allowed_domains:
        raise SsoError(f"{email} is not in an allowed domain", 403)
    role = map_role(cfg, claims)
    if role is None:
        raise SsoError(f"{email} is not in any group that has access to RAGOps", 403)
    org = svc.get_or_create_org(db, cfg.org)
    sess = SsoSession(prefix="pending", token_hash="", email=email, name=claims.get("name"), org_id=org.id, role=role,
                      expires_at=utcnow() + timedelta(hours=cfg.session_hours))
    db.add(sess)
    db.flush()
    handoff = secrets.token_urlsafe(24)
    db.add(OidcLoginState(state=hash_key(handoff)[:64], nonce="handoff", code_verifier=sess.id, return_to=return_to))
    svc.audit(db, None, f"sso:{email}", "sso.login", {"org": org.name, "role": role})
    ui = os.environ.get("RAGOPS_PUBLIC_URL", "").rstrip("/") + "/ui/"
    return f"{ui}#/sso?code={handoff}"


def exchange(db: Session, handoff: str) -> dict:
    """Swap the one-time code for a session token (shown once)."""
    from ragops.models import Organization

    st = db.get(OidcLoginState, hash_key(handoff)[:64])
    if st is None or st.nonce != "handoff" or st.created_at < utcnow() - HANDOFF_TTL:
        raise SsoError("sign-in code expired or already used", 400)
    sess = db.get(SsoSession, st.code_verifier)
    db.delete(st)
    raw = "rgs_" + secrets.token_urlsafe(32)
    sess.prefix, sess.token_hash = raw[:12], hash_key(raw)
    org = db.get(Organization, sess.org_id)
    return {"token": raw, "email": sess.email, "name": sess.name, "role": sess.role, "org": org.name if org else None,
            "expires_at": sess.expires_at.isoformat()}


def resolve_session(db: Session, raw: str) -> SsoSession | None:
    s = db.scalar(select(SsoSession).where(SsoSession.prefix == raw[:12], SsoSession.revoked.is_(False)))
    if s is None or s.token_hash != hash_key(raw) or s.expires_at < utcnow():
        return None
    return s


def logout(db: Session, raw: str) -> bool:
    s = resolve_session(db, raw)
    if s is None:
        return False
    s.revoked = True
    return True
