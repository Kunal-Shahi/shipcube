# shipcube-profiler

Runs an app on sample input and measures CPU, RSS, I/O, wall time, process count and outbound network calls, then maps the profile (plus `expected_users` / `schedule` hints) onto the classifier's features.

- `materialize(source, dir)`: `inline`, `git`, `upload` sources (`image` → `NotRunnable`)
- `summarize(dir)`: static inspection (entrypoint, framework, routes, libraries)
- `LocalRunner`: subprocess + psutil + rlimits + no-egress socket hook. Development only, not a security boundary.
- `CloudRunJobRunner`: production design (gen2 Cloud Run job, no egress); `job_spec()` implemented, `run()` stubbed
- `StaticRunner`: typical numbers when code can't run

Sample apps used by tests and the demo: `samples/dashboard` (Flask, bursty) and `samples/nightly_batch` (nightly job).
See [docs/research/load-estimation.md](../../docs/research/load-estimation.md) §4.
