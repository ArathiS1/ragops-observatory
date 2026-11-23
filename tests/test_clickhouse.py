"""ClickHouse backend, executed against the real ClickHouse engine embedded in chdb (no server needed).

The HTTP client is pointed at a mock transport that runs each statement in a chdb session, so the DDL, async inserts
and analytics SQL are all validated by ClickHouse itself.
"""

import httpx
import pytest

chdb = pytest.importorskip("chdb")


@pytest.fixture()
def ch(tmp_path):
    from chdb import session

    from ragops.clickhouse import ClickHouse

    sess = session.Session(str(tmp_path / "ch"))

    def handler(req: httpx.Request) -> httpx.Response:
        sql = req.content.decode()
        try:
            out = sess.query(sql)
        except Exception as e:  # noqa: BLE001
            return httpx.Response(500, text=str(e))
        return httpx.Response(200, text=str(out))

    client = ClickHouse("http://default:@clickhouse:8123/ragops", transport=httpx.MockTransport(handler))
    client.async_inserts = False  # embedded chdb has no async-insert queue; production ClickHouse uses it
    yield client
    sess.close()


def test_init_insert_and_analytics(ch):
    ch.init()
    now = ch.query("SELECT toString(now64(3) - INTERVAL 1 HOUR) AS n")[0]["n"]
    rows = []
    for i in range(30):
        fail = i % 3 == 0
        rows.append({"project_id": "p1", "trace_id": f"t{i}", "ts": now, "config_label": "v2" if i < 10 else "v1",
                     "source": "sdk", "model": "gpt-4o-mini", "latency_ms": 800 + i, "cost_usd": 0.001,
                     "user_feedback": None, "eval_status": "done", "is_failure": int(fail),
                     "root_cause": "ranking_failure" if fail else "ok", "confidence": 0.8,
                     "metrics": {"faithfulness": 0.9, "answer_relevance": 0.4 if fail else 0.8},
                     "n_context": 3, "top_score": 0.6, "updated_at": now})
    ch.insert_rows(rows, async_insert=False)
    # a re-processed trace replaces the old row (ReplacingMergeTree + FINAL)
    ch.insert_rows([{**rows[1], "is_failure": 1, "root_cause": "generation_failure", "updated_at": ch.query("SELECT toString(now64(3)) AS n")[0]["n"]}],
                   async_insert=False)
    a = ch.analytics("p1", 30)
    assert a["backend"] == "clickhouse"
    assert a["totals"]["traces"] == 30 and a["totals"]["failures"] == 11
    assert a["root_causes"] == {"ranking_failure": 10, "generation_failure": 1}
    assert {c["config_label"] for c in a["by_config"]} == {"v1", "v2"}
    assert a["daily"][0]["traces"] == 30 and a["totals"]["latency_p95_ms"] > 800


def test_mirror_from_worker_and_api(ch, client, project, monkeypatch):
    from ragops import clickhouse
    from tests.conftest import demo_trace

    ch.init()
    clickhouse.reset_client(ch)
    monkeypatch.setattr("ragops.clickhouse.get_clickhouse", lambda: ch)
    monkeypatch.setattr("ragops.services.clickhouse", clickhouse, raising=False)
    name, h = project
    traces = [demo_trace(q, id=f"m{i}", config_label="cfg-a") for i, q in enumerate(
        ["How much does the Pro plan cost per user?", "How long are audit logs retained?",
         "Does the mobile app work offline on Android?"])]
    client.post(f"/v1/projects/{name}/traces/batch", json={"traces": traces}, headers=h)
    a = client.get(f"/v1/projects/{name}/analytics", headers=h).json()
    assert a["backend"] == "clickhouse" and a["totals"]["traces"] == 3
    assert a["by_config"][0]["config_label"] == "cfg-a"
    clickhouse.reset_client(None)


def test_sql_fallback_shape(client, project):
    from tests.conftest import demo_trace

    name, h = project
    client.post(f"/v1/projects/{name}/traces", json=demo_trace("How long are audit logs retained?", config_label="x"),
                headers=h)
    a = client.get(f"/v1/projects/{name}/analytics", headers=h).json()
    assert a["backend"] == "sql" and a["totals"]["traces"] == 1 and a["by_config"][0]["config_label"] == "x"
