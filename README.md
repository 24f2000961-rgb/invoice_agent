# Invoice Action Agent (A2A 1.0)

FastAPI + SQLite implementation of the A2A invoice-action-agent surface described
in the spec: Agent Card discovery, `message:send`, task read/list, cancel, batched
AI decisioning via [AI Pipe](https://aipipe.org), idempotency, terminal-state
locking, and per-principal isolation.

## Layout

```
app/
  config.py     env-driven settings (tokens, base URL, AI Pipe)
  canon.py      canonical-JSON hashing for idempotency + package-content cache
  storage.py    SQLite: tasks, idempotency table, package decision cache
  schemas.py    pydantic validation for Proposal / Result
  ai_client.py  single batched call to AI Pipe's chat/completions, strict-JSON parse
  auth.py       Bearer -> principal
  card.py       Agent Card builder
  main.py       the five A2A routes
```

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in VALID_TOKENS, AIPIPE_TOKEN, BASE_URL
export $(cat .env | xargs)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Locally this serves the card at `http://localhost:8000/.well-known/agent-card.json`
and the API under `http://localhost:8000/a2a/...` (matching `BASE_URL=.../a2a`).

## Deploying so the grader can reach it

I can't provision a public HTTPS endpoint from this sandbox (no outbound network
here), so you'll need to host it — any of these work with zero code changes:

- **Render / Railway / Fly.io**: point at this repo, set the env vars from
  `.env.example`, they give you a free HTTPS URL automatically (`https://foo.onrender.com`).
- **A VM behind a reverse proxy** (Caddy/nginx) with a real TLS cert.

Whatever URL you get, set `BASE_URL` to `https://<that-host>/a2a` (no trailing
slash, no query/fragment) — this must be byte-identical to what you submit and to
what appears in `supportedInterfaces` in the card, which `card.py` builds from
`BASE_URL` automatically.

## Design notes / where the graded behaviors live

- **Idempotency** (`main.py::message_send`, `canon.py`): keyed on
  `(Bearer principal, messageId)`, hashed over the message only (configuration
  ignored) via recursively key-sorted compact JSON. Same hash → replay the stored
  response verbatim, no recompute. Different hash → `409 IDEMPOTENCY_CONFLICT`.
- **Package decision cache** (`storage.py::package_cache`): keyed by canonical
  package content hash, independent of batch/message/task id, so repeated
  Check/Save runs over the same 60 personalized packages cost zero model calls.
  All *uncached* packages in one request are still sent to the model in a single
  batched call (`ai_client.decide_packages`).
- **Action gate**: `message:send` on a batch only ever returns
  `TASK_STATE_INPUT_REQUIRED` with a proposals artifact — no receipts artifact,
  no execution, until a matching results continuation arrives.
- **Result continuation validation** (`_handle_results`): checks principal
  (via task ownership lookup), task id, context id, batch id, and per-item
  packageId+actionId+action against the stored proposal before ever building an
  execution. Rejected outcomes are recorded in history but never executed.
  Facts/evidence in each execution are copied from the *stored proposal*, never
  re-derived from the grader's message, so a receipt can't be forged by the
  continuation payload.
- **Terminal immutability + cancel/receipt race**: every task mutation
  (`_handle_results`, `cancel_task`) runs inside `storage.txn()`, which holds a
  single process-wide lock for the duration of the read-check-write. Whichever
  request observes the task as non-terminal first wins and flips it to a
  terminal state; the other re-checks under the same lock, finds it already
  terminal, and returns `409`. Only one of the two ever returns `200`.
- **User isolation**: every task lookup filters on `principal == token`.
  Cross-user reads/cancels/continuations return a generic `404 NOT_FOUND` (task
  id never echoed), and `GET /tasks` only ever returns the caller's own rows.

## Known simplifications (given the environment I built this in)

- Concurrency control is a single process-wide lock rather than DB-level row
  locking — correct for one worker process; if you deploy with multiple worker
  processes you'd want `SELECT ... FOR UPDATE`-style locking instead (swap
  SQLite for Postgres and use `storage.py` as the seam).
- I could not run this against a live AI Pipe token or the real grader from this
  sandbox (no outbound network access here), so I verified it by syntax-checking
  every module (`python -m py_compile`) rather than an end-to-end request. Please
  smoke-test `message:send` against a couple of your own sample packages before
  pointing the grader at it.
