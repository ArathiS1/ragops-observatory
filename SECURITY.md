# Security policy

Please report vulnerabilities privately through GitHub Security Advisories ("Report a vulnerability" on the
repository's Security tab). Do not open public issues for security problems. You will get an answer within 5 days.

## Hardening checklist for production

- Set `RAGOPS_ADMIN_KEY` to a long random value; the default development key logs a warning on startup.
- Give services **ingest** keys (write-only). Dashboards and CI use **viewer** keys.
- Enable PII redaction for every field that can contain user data, or set `store_raw_text: false` to keep only
  salted hashes. Set `retention_days` to your policy.
- Run behind TLS (ingress / load balancer). API keys are stored as SHA-256 hashes and shown once at creation.
- The audit log records project, key, settings, corpus, golden-set and SLO changes.
