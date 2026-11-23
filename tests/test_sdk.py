from fastapi.testclient import TestClient

from ragops import RagopsClient, observe_rag
from ragops.demo import pipeline as pl


def _sdk(client, project_headers, name="p1") -> RagopsClient:
    rg = RagopsClient("http://testserver", api_key="x", project=name, sync=True)
    rg._http = TestClient(client.app, headers=project_headers)
    return rg


def test_context_manager_trace(client, project):
    name, h = project
    rg = _sdk(client, h)
    q = "How often does Nimbus back up customer data?"
    with rg.trace(q, config_label="sdk-v1", user="u1") as t:
        out = pl.run(q)
        t.retrieved(out["retrieved"])
        t.context(out["contexts"])
        t.generation(out["answer"], model="demo", prompt_tokens=10, completion_tokens=5)
    got = client.get(f"/v1/projects/{name}/traces/{t.id}", headers=h).json()
    assert got["config"]["label"] == "sdk-v1" and got["metadata"]["user"] == "u1"
    assert got["latency_ms"] is not None and got["diagnosis"]["label"] == "ok"
    assert rg.feedback(t.id, -1, "wrong")


def test_decorator_and_framework_objects(client, project):
    name, h = project
    rg = _sdk(client, h)

    class LCDoc:  # duck-typed LangChain Document
        def __init__(self, text, doc_id):
            self.page_content, self.metadata = text, {"doc_id": doc_id, "chunk_id": f"{doc_id}#0"}

    @observe_rag(rg, config_label="decorated")
    def answer(query):
        return {"answer": "Deleted files stay in the trash for 30 days.",
                "contexts": [LCDoc("Deleted files stay in the trash for 30 days and can be restored.", "data-retention")]}

    answer("How long do deleted files stay in the trash?")
    rows = client.get(f"/v1/projects/{name}/traces", headers=h).json()
    t = client.get(f"/v1/projects/{name}/traces/{rows[0]['id']}", headers=h).json()
    assert t["chunks"][0]["doc_id"] == "data-retention"


def test_sdk_never_raises_when_server_is_down():
    rg = RagopsClient("http://127.0.0.1:9", api_key="x", project="p", timeout=0.2, sync=True)
    with rg.trace("hello") as t:
        t.generation("hi")
    assert rg.dropped == 1


def test_sdk_exception_in_app_is_recorded_and_reraised(client, project):
    name, h = project
    rg = _sdk(client, h)
    try:
        with rg.trace("boom"):
            raise KeyError("x")
    except KeyError:
        pass
    rows = client.get(f"/v1/projects/{name}/traces", headers=h).json()
    t = client.get(f"/v1/projects/{name}/traces/{rows[0]['id']}", headers=h).json()
    assert "KeyError" in t["metadata"]["error"]


def test_background_batching(client, project):
    name, h = project
    rg = RagopsClient("http://testserver", api_key="x", project=name, flush_interval=60)
    rg._http = TestClient(client.app, headers=h)
    for i in range(5):
        rg.log(f"How long are audit logs retained? {i}", "1 year.", ["Audit logs are retained for 1 year."])
    rg.flush()
    assert len(client.get(f"/v1/projects/{name}/traces", headers=h).json()) == 5
    rg._stop.set()
