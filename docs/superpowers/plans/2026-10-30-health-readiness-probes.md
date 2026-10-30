# Health and Readiness Probes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the liveness and readiness concerns of `/api/health` so that readiness live-probes PostgreSQL and InfluxDB, validates the InfluxDB token, and returns 503 when either dependency is unusable.

**Architecture:** A new `backend/services/health_service.py` owns three functions — one probe per dependency plus an aggregator that returns a body and an HTTP status. `routes.py` stays thin and only calls the aggregator. The module imports `Config` and the two database libraries and nothing from `analytics_service`, so its unit tests run with no app boot and no live dependency.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy 2.x + psycopg2, influxdb-client 1.50.0, pytest 8.3.4.

**Spec:** `docs/superpowers/specs/2026-10-30-health-readiness-design.md`

## Global Constraints

- No new runtime dependency. `pytest==8.3.4` only, in a new `requirements-dev.txt`, pinned with `==`.
- Timeout budget per dependency comes from `Config.HEALTH_TIMEOUT_SECONDS`, default `2.0`.
- `reason` is only ever `"unreachable"`, `"timeout"`, or `"unhealthy"`.
- No response body may contain exception text, `HealthCheck.message`, a hostname, a URL, or a token.
- Real errors are logged server-side using the existing `[ERROR]` print convention — surface, never swallow.
- All print statements stay ASCII-only.
- `health_service.py` imports Config as `from backend.config import Config` (the style `run.py` uses), never the `sys.path` hack used in `data_loader.py`.

## Review Focus

- `HEALTH_TIMEOUT_SECONDS` below `1.0` truncates to `connect_timeout=0`, which libpq reads as "wait forever" — the opposite of a timeout. Clamped and tested in Task 2.
- The InfluxDB token defaults to an empty string in `Config`, so an unconfigured install must report `unhealthy`, not `up`. Tested in Task 3.
- A probe that fails must still dispose its engine and close its client, or a polled endpoint leaks file descriptors. Tested in Tasks 2 and 3.
- Nothing in the serialised JSON may contain a secret or an exception string, asserted against the raw response bytes rather than a parsed dict. Tested in Task 5.
- Both dependencies down must report both reasons rather than short-circuiting on the first failure. Tested in Task 4.

---

### Task 1: Test harness and liveness endpoint

**Files:**
- Create: `requirements-dev.txt`, `pytest.ini`, `tests/conftest.py`, `tests/test_health_routes.py`
- Modify: `backend/api/routes.py` (the `health` handler)

**Interfaces:**
- Consumes: nothing.
- Produces: an `app` pytest fixture returning a configured Flask app, and a `client` fixture returning `app.test_client()`.

- [ ] **Step 1: Write the failing test**

```python
def test_health_returns_alive(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "alive"}
```

`tests/conftest.py` must set `os.environ` for `DATABASE_URL`, `DB_PASSWORD` and `INFLUX_URL`
*before* any `backend` import, because `backend/config.py` raises at import time without the first
two, and importing `analytics_service` performs a real InfluxDB health call at module load. Point
`INFLUX_URL` at a closed port such as `http://127.0.0.1:1` so that boot-time call fails in
milliseconds instead of waiting out the client's 10 second default. `load_dotenv` does not override
variables already present in the environment, so these assignments win over `.env`.

`pytest.ini` sets `pythonpath = .` and `testpaths = tests` so that `import backend` resolves from
the repo root.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_health_routes.py::test_health_returns_alive -v`
Expected: FAIL — the body is still the old `{"status": "ok"}`.

- [ ] **Step 3: Change the `health` handler in `backend/api/routes.py` to return `{"status": "alive"}`**

No I/O and no new imports. The handler stays a one-liner.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_health_routes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add requirements-dev.txt pytest.ini tests/ backend/api/routes.py
git commit -m "test: add pytest harness; make /api/health a pure liveness probe"
```

---

### Task 2: PostgreSQL readiness probe

**Files:**
- Create: `backend/services/health_service.py`, `tests/test_health_service.py`
- Modify: `backend/config.py` (add `HEALTH_TIMEOUT_SECONDS`)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `REASON_UNREACHABLE = "unreachable"`, `REASON_TIMEOUT = "timeout"`, `REASON_UNHEALTHY = "unhealthy"`
  - `check_postgres(timeout_s: float | None = None) -> dict` — returns `{"status": "up", "latency_ms": int}` or `{"status": "down", "reason": str}`. Never raises.

- [ ] **Step 1: Write the failing tests**

```python
def test_check_postgres_up_on_successful_select(monkeypatch):
    # fake engine whose connect().execute() succeeds
    # -> status == "up" and latency_ms >= 0

def test_check_postgres_down_when_engine_raises(monkeypatch):
    # create_engine raises OperationalError
    # -> {"status": "down", "reason": "unreachable"}

def test_check_postgres_never_raises(monkeypatch):
    # create_engine raises RuntimeError("boom") -> returns a dict, does not propagate

def test_check_postgres_disposes_engine_on_failure(monkeypatch):
    # engine.connect() raises -> engine.dispose() was still called

def test_connect_timeout_never_zero(monkeypatch):
    # timeout_s=0.4 -> connect_args passed to create_engine has connect_timeout >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_health_service.py -v`
Expected: FAIL with `ModuleNotFoundError` for `backend.services.health_service`.

- [ ] **Step 3: Implement `check_postgres` in `backend/services/health_service.py`, and add `HEALTH_TIMEOUT_SECONDS` to `Config` reading `HEALTH_TIMEOUT_SECONDS` from the environment with default `2.0`**

Build a throwaway engine with `poolclass=NullPool` and `connect_args={"connect_timeout": max(1, int(budget))}`, execute `text("SELECT 1")`, and dispose in a `finally`. On exception, log `[ERROR]` server-side and map to `REASON_TIMEOUT` when elapsed time has reached the budget, otherwise `REASON_UNREACHABLE`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_health_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/health_service.py backend/config.py tests/test_health_service.py
git commit -m "feat: add PostgreSQL readiness probe with bounded connect timeout"
```

---

### Task 3: InfluxDB readiness probe with token validation

**Files:**
- Modify: `backend/services/health_service.py`, `tests/test_health_service.py`

**Interfaces:**
- Consumes: the three `REASON_*` constants from Task 2.
- Produces: `check_influx(timeout_s: float | None = None) -> dict` — the same return shape as `check_postgres`. Never raises.

- [ ] **Step 1: Write the failing tests**

```python
def test_check_influx_up_when_ping_and_bucket_succeed(monkeypatch):
    # ping() True, find_bucket_by_name returns a bucket -> status == "up"

def test_check_influx_unreachable_when_ping_false(monkeypatch):
    # ping() False, elapsed well under budget -> reason == "unreachable"

def test_check_influx_unhealthy_when_bucket_missing(monkeypatch):
    # ping() True, find_bucket_by_name returns None -> reason == "unhealthy"

def test_check_influx_unhealthy_when_token_rejected(monkeypatch):
    # ping() True, find_bucket_by_name raises ApiException(status=401) -> reason == "unhealthy"

def test_check_influx_unhealthy_with_empty_token(monkeypatch):
    # token configured as "" and bucket lookup raises 401
    # -> reason == "unhealthy", and status is never "up"

def test_check_influx_closes_client_on_failure(monkeypatch):
    # bucket lookup raises -> client.close() was still called
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_health_service.py -v`
Expected: FAIL — `check_influx` is not defined.

- [ ] **Step 3: Implement `check_influx` in `backend/services/health_service.py`**

Construct the client with `timeout=int(budget * 1000)` — the influxdb-client `timeout` parameter is
in **milliseconds**, not seconds. Call `ping()` first: a `False` result means `REASON_UNREACHABLE`,
or `REASON_TIMEOUT` if elapsed has reached the budget. Then call
`client.buckets_api().find_bucket_by_name(...)` with the configured bucket; a `None` result or any
exception from this authenticated step maps to `REASON_UNHEALTHY`. `ping()` deliberately comes
first and `health()` is never called, because `/ping` is unauthenticated and `HealthCheck.message`
would carry raw server text. Log the real error `[ERROR]` server-side. Close the client in a
`finally`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_health_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/health_service.py tests/test_health_service.py
git commit -m "feat: add InfluxDB readiness probe that validates the token"
```

---

### Task 4: Readiness aggregation

**Files:**
- Modify: `backend/services/health_service.py`, `tests/test_health_service.py`

**Interfaces:**
- Consumes: `check_postgres`, `check_influx`.
- Produces: `check_readiness() -> tuple[dict, int]` — the response body and the HTTP status code. The body is `{"status": "ready" | "not_ready", "checks": {"postgresql": {...}, "influxdb": {...}}}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_readiness_ready_when_both_up(monkeypatch):
    # -> ({"status": "ready", ...}, 200)

def test_readiness_not_ready_when_postgres_down(monkeypatch):
    # -> status code 503

def test_readiness_not_ready_when_influx_down(monkeypatch):
    # -> status code 503

def test_readiness_reports_both_reasons_when_both_down(monkeypatch):
    body, code = check_readiness()
    assert code == 503
    assert body["checks"]["postgresql"]["reason"] == "unreachable"
    assert body["checks"]["influxdb"]["reason"] == "unhealthy"

def test_reason_vocabulary_is_closed(monkeypatch):
    # every reason produced across the down cases is in
    # {"unreachable", "timeout", "unhealthy"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_health_service.py -v`
Expected: FAIL — `check_readiness` is not defined.

- [ ] **Step 3: Implement `check_readiness` in `backend/services/health_service.py`**

Call both probes unconditionally so a caller always learns the state of both; no short-circuit. The status is `200` only when every check reports `up`, otherwise `503`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_health_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/health_service.py tests/test_health_service.py
git commit -m "feat: aggregate dependency probes into a readiness verdict"
```

---

### Task 5: Wire /api/ready and prove nothing leaks

**Files:**
- Modify: `backend/api/routes.py`, `tests/test_health_routes.py`

**Interfaces:**
- Consumes: `check_readiness() -> tuple[dict, int]`.
- Produces: the `GET /api/ready` route.

- [ ] **Step 1: Write the failing tests**

```python
def test_ready_200_when_all_up(client, monkeypatch):
    # both probes patched up -> 200 and status == "ready"

def test_ready_503_when_any_down(client, monkeypatch):
    # influx probe patched down -> 503 and status == "not_ready"

def test_health_performs_no_dependency_io(client, monkeypatch):
    # patch both probes to raise AssertionError if called
    # GET /api/health must still return 200

def test_ready_body_leaks_no_server_text(client, monkeypatch):
    # a probe raises Exception("FATAL: password=hunter2 host=10.0.0.5 token=abc123")
    body = client.get("/api/ready").data.decode()
    for secret in ("hunter2", "10.0.0.5", "abc123", "FATAL", "password"):
        assert secret not in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_health_routes.py -v`
Expected: FAIL — 404 on `/api/ready`.

- [ ] **Step 3: Add the `ready` handler to `backend/api/routes.py`**

Route `GET /api/ready`, call `check_readiness()`, return `jsonify(body), status`. Do not add a
try/except that formats an exception into the response — the probes already return sanitised dicts,
and catching here would reintroduce the `str(e)` leak this endpoint exists to avoid.

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest -v`
Expected: PASS for every test.

- [ ] **Step 5: Commit**

```bash
git add backend/api/routes.py tests/test_health_routes.py
git commit -m "feat: add GET /api/ready returning 503 when a dependency is down"
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/known_issues.md`, `CLAUDE.md`

- [ ] **Step 1: Add the false-log entry to `docs/known_issues.md`**

`PostgreSQLLoader.__init__` prints a PostgreSQL-connected line immediately after `create_engine()`,
which is lazy and opens no connection, so the message proves nothing about reachability and reads
as a successful connection even when the database is unreachable. Name the function rather than a
line number, matching the file's convention. Also mark the `/api/health` entry as resolved by this
change.

- [ ] **Step 2: Update `CLAUDE.md`**

Three edits: the "No test suite exists" convention is now false, so replace it with how to run the
suite (`python -m pytest`) and how to install `requirements-dev.txt`; add `HEALTH_TIMEOUT_SECONDS`
(default `2.0`) to the optional keys in the Config section; and add `/api/ready` beside
`/api/health` under Entry points and in the `routes.py` row of the Layout table.

- [ ] **Step 3: Verify the documented command actually works**

Run: `python -m pytest`
Expected: PASS — this is the exact command CLAUDE.md now advertises.

- [ ] **Step 4: Commit**

```bash
git add docs/known_issues.md CLAUDE.md
git commit -m "docs: record the false PostgreSQL connect log; document the test suite"
```
