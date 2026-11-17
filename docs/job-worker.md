# Job worker: `PRKS_EMBEDDED_WORKER` and `prks worker`

L-J-1 (#627); design in `docs/postgresql-architecture.md` §12. Every PRKS
deployment runs on PostgreSQL (PG-C2 #641), so every web process has a job
worker: its embedded one or a separate `prks worker`.

Background jobs (`prks.jobs`) are claimed and run by a worker. A worker
starts claiming only once the database applied exactly this code's schema
files (`python prks_app.py migrate`): while a file is pending it waits and
says why in its log (`worker_waiting reason=pending`); a database newer than
the code stops it.

## The embedded worker (the default)

With `PRKS_AUTH` set, the web process (`python prks_app.py`) runs one worker
thread beside the HTTP server:

| `PRKS_EMBEDDED_WORKER` | Effect |
| --- | --- |
| unset or `1` | the web process runs one worker thread (default) |
| `0` | no worker in the web process; jobs wait for a separate `prks worker` |
| anything else | the web process refuses to start (exit status 2) |

- It starts after the web start check (the §11.1 pairing check and
  start-generation bump, which run once per process); it never bumps again.
- It uses its own small connection pool (`application_name` `prks-worker`,
  4 connections) plus one `LISTEN` connection, beside the web pool.
- On shutdown (SIGTERM, SIGINT or Ctrl-C) the server closes its listening
  socket at once (new connections are refused; requests already being
  handled keep running) and the worker stops gracefully: it claims nothing more, the
  running job may finish within 30 seconds, then the job is given back to
  the queue without spending an attempt. A second signal during that wait
  ends the process at once; the job's lease then expires and it is
  reclaimed.
- A worker that cannot start (for example a database newer than the code)
  is logged (`embedded_worker_refused reason=…`) and shown in the
  diagnostics; the web process keeps serving.

## `prks worker` (a separate process)

```
python prks_app.py worker [--storage-root /srv/prks] [--testing]
```

It acts on the storage root `prks_app.py` selects for every role
(`--storage-root`, `PRKS_STORAGE` or the bootstrap file), so `migrate`,
`serve` and `worker` of one deployment name it the same way.
`python -m backend.jobs.worker --root /srv/prks [--testing]` is the same
worker with the root given directly.

It joins a data root the web process already bound and holds that root's
**shared** lease while it runs. The web process holds the root shared too,
so `prks worker` runs beside it (set `PRKS_EMBEDDED_WORKER=0` on that web
process). A worker that meets a web process at the moment it binds a new
root is refused; started again, it joins. Exit status: 0 after a graceful stop
(SIGTERM or SIGINT), 1 when the start check refuses, 2 for a usage or
configuration error. Output is fixed text, never a connection string.

## Diagnostics

The Library owner can read `GET /api/diagnostics/jobs` (other roles get
403). Contract:
`docs/api/openapi-job-diagnostics.json`.

- `embedded_worker`: `null` with `PRKS_EMBEDDED_WORKER=0`; otherwise its
  `state` (`starting`, `waiting`, `running`, `stopping`, `stopped` or
  `refused`), a stable `reason` code while waiting or refused, and the
  process-local counters since start (claimed, succeeded, retried,
  cancelled, failed, lost, released, reaped, errors).
- `queue`: queued, running and failed jobs by kind and state, with the age
  of the oldest in seconds.

Only counts, ages, job kinds and reason codes: never a payload, an error
text, a Library id or other Library metadata.
