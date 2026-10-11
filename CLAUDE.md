Read SPEC.md. Current milestone: M4 (real model runs).
M0-M2 and M6 are done; M3-M5 tooling is done. The remaining checks (a real model end to end, the 3x3
canonical and variant evals) need API keys in .env: run `python -m harness.preflight`, then
`python -m harness.run_eval --parallel 3`, then `python -m report.make_report`.
Decisions on spec ambiguities are logged in DECISIONS.md (items marked [review] need the owner).
