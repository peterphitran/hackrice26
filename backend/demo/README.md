# Lou Demo Rehearsal

The reliable live path is local: graph-guided checkout workload selection,
baseline/candidate measurement, PostgreSQL evidence, an offline recorded patch
proposal, and independent fix verification. Docker Desktop is the only runtime
dependency outside the Python environment.

```bash
cd backend
python -m lou.verification.int003
```

The controller prints `__LOU_INT003_RESULT__`. It succeeds only when the real
good patch passes both selected workloads and the deliberate no-op patch fails
the same verification. No model API, GitHub token, UI, or webhook participates.

## Record the fallback

Copy the successful good-case `run_id` from the controller output, then create
the presentation fallback. These files are generated under `.lou/` and remain
local so they never contain a teammate's database artifacts in Git.

```bash
python -m scripts.record_demo_fallback --run <good-run-id>
```

The fallback contains the same JSON evidence report used by `lou report`, a
Markdown rendering for presentation, and a SHA-256 manifest. During a demo,
show the live command first; if Docker or optional integrations fail, open the
saved Markdown report and state that it is a recorded output of the same report
reader.

## Cross-machine check

On a machine that has Python dependencies and Docker Desktop available, run:

```bash
cd backend
python -m scripts.rehearse_clean_clone
```

This clones only committed files into a disposable directory and executes
INT-002 against a separate local Postgres port. Record the printed ref and
result marker in the team demo notes. A second teammate must run this command
before INT-004 can be marked complete.
