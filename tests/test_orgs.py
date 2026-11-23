"""Organisation-level tenancy: org keys span projects, and organisations are isolated from each other."""

from tests.conftest import ADMIN, demo_trace


def _org(client, name):
    r = client.post("/v1/orgs", json={"name": name}, headers=ADMIN)
    assert r.status_code == 201
    return {"Authorization": f"Bearer {r.json()['admin_key']}"}


def test_org_keys_span_projects_and_orgs_are_isolated(client):
    acme, globex = _org(client, "acme"), _org(client, "globex")
    # org admins create projects in their own org only
    r = client.post("/v1/projects", json={"name": "acme-support"}, headers=acme)
    assert r.status_code == 201 and r.json()["org"] == "acme"
    client.post("/v1/projects", json={"name": "acme-sales"}, headers=acme)
    assert client.post("/v1/projects", json={"name": "x", "org": "globex"}, headers=acme).status_code == 403
    client.post("/v1/projects", json={"name": "globex-hr"}, headers=globex)
    # an org viewer key reads every project in its org, nothing outside it
    viewer = client.post("/v1/orgs/acme/keys", json={"name": "dash", "role": "viewer"}, headers=acme).json()["key"]
    hv = {"Authorization": f"Bearer {viewer}"}
    assert {p["name"] for p in client.get("/v1/projects", headers=hv).json()} == {"acme-support", "acme-sales"}
    assert client.get("/v1/projects/acme-sales/overview", headers=hv).status_code == 200
    assert client.get("/v1/projects/globex-hr/overview", headers=hv).status_code == 403
    assert client.patch("/v1/projects/acme-sales/settings", json={"sample_rate": 0.5}, headers=hv).status_code == 403
    assert client.get("/v1/orgs/globex", headers=acme).status_code == 403
    # org ingest key can send traces to any project in the org
    ing = client.post("/v1/orgs/acme/keys", json={"name": "svc", "role": "ingest"}, headers=acme).json()["key"]
    r = client.post("/v1/projects/acme-support/traces", json=demo_trace("How long are audit logs retained?"),
                    headers={"Authorization": f"Bearer {ing}"})
    assert r.status_code == 202
    ov = client.get("/v1/orgs/acme", headers=hv).json()
    assert {p["name"] for p in ov["projects"]} == {"acme-support", "acme-sales"} and ov["traces"] == 1
    # revoking works; instance admin sees all orgs
    kid = next(k["id"] for k in client.get("/v1/orgs/acme/keys", headers=acme).json() if k["name"] == "dash")
    client.delete(f"/v1/orgs/acme/keys/{kid}", headers=acme)
    assert client.get("/v1/projects", headers=hv).status_code == 401
    assert {o["name"] for o in client.get("/v1/orgs", headers=ADMIN).json()} >= {"acme", "globex"}
    assert client.post("/v1/orgs", json={"name": "z"}, headers=acme).status_code == 403


def test_existing_projects_join_default_org_and_can_move(client, project):
    name, h = project
    assert client.get("/v1/projects", headers=ADMIN).json()[0]["org"] == "default"
    _org(client, "acme")
    r = client.post(f"/v1/orgs/acme/projects/{name}/move", headers=ADMIN).json()
    assert r["org"] == "acme"
    # project keys keep working after the move
    assert client.get(f"/v1/projects/{name}/overview", headers=h).status_code == 200
