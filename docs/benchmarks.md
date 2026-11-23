# Benchmarks and case study

| | What it measures | Result (heuristic judge) |
|---|---|---|
| [Synthetic benchmark](https://github.com/ArathiS1/ragops-observatory/blob/main/benchmark/RESULTS.md) | Stage attribution on the demo help centre, one injected failure per case | 89.6% accuracy, 3.6% false alarms |
| [SQuAD 2.0 benchmark](https://github.com/ArathiS1/ragops-observatory/blob/main/benchmark/SQUAD_RESULTS.md) | Same, on 30 real Wikipedia articles (2,728 chunks) with a real retriever | 73.8% accuracy, 21.7% false alarms |
| [Case study](https://github.com/ArathiS1/ragops-observatory/blob/main/examples/case-study/CASE_STUDY.md) | A real RAG service over 10 days, nothing injected, scored against SQuAD ground truth | 88% of flagged answers were really wrong; 34 silent stale answers caught |

Reproduce: `ragops benchmark`, `ragops benchmark --dataset squad`, `python examples/case-study/run_case_study.py`.
The numbers are deliberately reported with the free, deterministic heuristic judge so anyone can reproduce them
exactly; an LLM judge (`RAGOPS_JUDGE=openai`) mainly reduces false alarms on paraphrased answers.
