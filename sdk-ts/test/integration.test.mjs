// Runs against a live RAGOps server: RAGOPS_URL, RAGOPS_API_KEY (project admin key), RAGOPS_PROJECT.
import { test } from "node:test";
import assert from "node:assert/strict";
import { RagopsClient } from "../dist/index.js";

const ready = process.env.RAGOPS_URL && process.env.RAGOPS_API_KEY && process.env.RAGOPS_PROJECT;

test("end to end against a live server", { skip: !ready && "set RAGOPS_URL, RAGOPS_API_KEY, RAGOPS_PROJECT" }, async () => {
  const rg = new RagopsClient({ sync: true });
  const corpus = await rg.upsertCorpus([{ doc_id: "ts-doc", text: "Deleted files stay in the trash for 30 days." }]);
  assert.ok(corpus.added + corpus.updated + corpus.unchanged + (corpus.rechunked ?? 0) >= 1);
  let id;
  await rg.trace("How long do deleted files stay in the trash?", { configLabel: "ts-sdk" }, async (t) => {
    id = t.id;
    t.forceEval();
    t.context([{ text: "Deleted files stay in the trash for 30 days.", doc_id: "ts-doc", chunk_id: "ts-doc#0" }]);
    t.generation("Deleted files stay in the trash for 30 days. [ts-doc]", { model: "ts-model" });
  });
  await rg.flush();
  assert.ok(await rg.feedback(id, 1));
  let detail;
  for (let i = 0; i < 40; i++) {
    detail = await rg.request("GET", `/v1/projects/${rg.project}/traces/${id}`);
    if (detail?.eval_status === "done") break;
    await new Promise((r) => setTimeout(r, 250));
  }
  assert.equal(detail.model, "ts-model");
  assert.equal(detail.diagnosis.label, "ok");
  const ov = await rg.overview(1);
  assert.ok(ov.traces >= 1);
  await rg.close();
});
