# Listing Web App — Phase 2 (FastAPI web layer) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap the already-built, already-tested Shopify→Myntra pipeline in a FastAPI web UI so non-technical teammates can generate Myntra sheets and fix rejected rows, with Cognito auth and SSM/Secrets config layered in (EC2 deploy deferred).

**Architecture:** A new presentation layer `src/web/` (FastAPI + Jinja + plain CSS + htmx) that *calls* the existing `src/myntra` and `src/core` Python functions directly — no change to that logic. A layered settings loader reads env vars first and falls back to SSM Parameter Store / Secrets Manager, and an `AUTH_DISABLED=1` flag bypasses Cognito locally, so the whole app builds and tests with no AWS reachable. Generate runs as an in-process background job tracked in an in-memory store and polled by htmx.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Jinja2, python-multipart, python-jose[cryptography], boto3 (existing), openpyxl/PyYAML (existing), htmx (vendored JS), pytest.

## Global Constraints

- **Python 3.12**; the app must import and run under `python:3.12-slim` (matches Dockerfile).
- **No Tailwind, no Node, no build step, no runtime CDN.** All CSS/JS/fonts are vendored under `src/web/static/`.
- **Do not modify** any file under `src/myntra/` or `src/core/` logic — the web layer only calls them. (The existing 41-test suite must still pass unchanged.)
- **Styling is the locked "Marigold Ops" palette:** bg `#191613`, panel `#221E1A`, accent marigold `#E8A33D`, success green `#7BB87A`; fonts Space Grotesk (display), IBM Plex Mono (data), Inter (body).
- **styleGroupId rule:** `reserve()` must NOT advance the counter; only the "Mark upload successful" confirm step calls `confirm()`, which advances it.
- **Fix-errors corrections are typed free-text**, validated against Myntra vocab via `mapper.validate_value` before writing; invalid values are reported as `rejected`, never written. No option buttons.
- **Tests for the web layer stub the pipeline/corrector** (no real image downloads, S3, or AWS calls in CI).
- Run all tests with `python -m pytest` from the repo root. Commit after each task.

### Backend interfaces this plan consumes (already implemented, verbatim signatures)

```python
# src/myntra/pipeline.py
def main(template_path=None, csv_path=None, out_dir="output",
         config_dir="config/myntra", fetch=None, upload=None,
         style_group_id_start=None) -> dict
   # returns {"filled": path, "report": path, "products": int, "uploaded": int}

# src/myntra/groupid_ledger.py
def read_ledger(store, key=LEDGER_KEY) -> dict
def reserve(store, count, filename, key=LEDGER_KEY) -> (start:int, batch_id:str)
def confirm(store, batch_id, key=LEDGER_KEY) -> int      # raises KeyError if no pending batch
class S3JsonStore:  # __init__(self, bucket, client); get_json(key); put_json(key, data)

# src/myntra/error_reader.py
@dataclass
class RowError: row:int; sku:str; status:str; cells:dict; issues:list
def load_rules(path="config/myntra/error_rules.yaml") -> dict
def read_errors(path, rules, sheet="Sarees") -> list[RowError]
   # each issue = {"category","action","explanation","field","raw"}
   # action in {"auto_fix","manual_choice","drop_sku","explain_only"}

# src/myntra/corrector.py
def plan_corrections(row_errors) -> {"auto":[sku], "drop":[sku], "manual":[{...}], "unknown":[{...}]}
def correct(row_errors, template, template_path, constants, answers, drops, out_path) -> summary
   # answers = {sku: {field: value}}; drops = set(sku)
   # summary = {"written":int, "dropped":[sku], "changed":{sku:[field]}, "rejected":{sku:[{field,value}]}}

# src/myntra/template_reader.py
def read_template(path) -> Template   # .col_index_by_header, .vocab_by_header, .headers

# src/myntra/mapper.py
def validate_value(value, vocab) -> canonical value or None
```

---

## File structure

```
requirements.txt                         # MODIFY: add web deps
src/web/__init__.py                       # CREATE
src/web/settings.py                       # CREATE: layered config + ledger store factory
src/web/auth.py                           # CREATE: AUTH_DISABLED bypass + Cognito JWT validation
src/web/jobs.py                           # CREATE: in-memory job store
src/web/main.py                           # CREATE: FastAPI app factory + router registration
src/web/routers/__init__.py               # CREATE
src/web/routers/pages.py                  # CREATE: landing/home + nav
src/web/routers/generate.py               # CREATE: Flow A
src/web/routers/fix.py                    # CREATE: Flow B
src/web/templates/base.html               # CREATE
src/web/templates/home.html               # CREATE
src/web/templates/generate.html           # CREATE
src/web/templates/_stepper.html           # CREATE (htmx fragment)
src/web/templates/_result.html            # CREATE (htmx fragment)
src/web/templates/fix.html                # CREATE
src/web/templates/_fix_review.html        # CREATE
src/web/templates/_fix_result.html        # CREATE
src/web/static/app.css                    # CREATE: Marigold Ops CSS (from mockups)
src/web/static/htmx.min.js                # CREATE: vendored htmx
src/web/runtime/.gitkeep                  # CREATE (per-job temp dirs live here, gitignored)
Dockerfile                                # MODIFY: CMD -> uvicorn
.gitignore                                # MODIFY: ignore src/web/runtime/*
tests/web/__init__.py                     # CREATE
tests/web/test_settings.py                # CREATE
tests/web/test_auth.py                    # CREATE
tests/web/test_jobs.py                    # CREATE
tests/web/test_pages.py                   # CREATE
tests/web/test_generate.py               # CREATE
tests/web/test_fix.py                     # CREATE
docs/runbooks/web-cognito-setup-console.md   # CREATE (Task 8)
docs/runbooks/web-ssm-secrets-setup-console.md # CREATE (Task 9)
```

---

## Task 1: Dependencies + layered Settings loader

**Files:**
- Modify: `requirements.txt`
- Create: `src/web/__init__.py` (empty)
- Create: `src/web/settings.py`
- Create: `tests/web/__init__.py` (empty)
- Test: `tests/web/test_settings.py`

**Interfaces:**
- Consumes: nothing (foundation).
- Produces:
  - `class Settings` with fields `s3_bucket, s3_region, s3_prefix, cognito_pool_id, cognito_client_id, cognito_client_secret, cognito_domain, cognito_redirect_uri, auth_disabled (bool), ledger_local_path (str|None)`.
  - `def load_settings(env: dict | None = None, ssm=None, secrets=None) -> Settings`
  - `def ledger_store(settings: Settings)` → returns a JSON store object with `get_json(key)`/`put_json(key, data)` (an `S3JsonStore` in cloud, a `LocalJsonStore` when `ledger_local_path` is set).
  - `class LocalJsonStore` (dev/offline: one JSON file on disk).

- [ ] **Step 1: Add dependencies**

Edit `requirements.txt` to append (keep existing lines):

```
fastapi
uvicorn[standard]
jinja2
python-multipart
python-jose[cryptography]
httpx
```

(`httpx` is FastAPI's `TestClient` dependency.) Then run `pip install -r requirements.txt`.

- [ ] **Step 2: Write the failing test**

Create `tests/web/__init__.py` (empty) and `tests/web/test_settings.py`:

```python
from src.web.settings import load_settings, ledger_store, LocalJsonStore


def test_env_takes_precedence_over_ssm():
    env = {"S3_BUCKET": "from-env", "S3_REGION": "ap-south-1", "AUTH_DISABLED": "1"}
    calls = []

    def fake_ssm(name):
        calls.append(name)
        return "from-ssm"

    s = load_settings(env=env, ssm=fake_ssm, secrets=lambda n: "secret")
    assert s.s3_bucket == "from-env"      # env wins
    assert s.auth_disabled is True
    assert calls == []                    # SSM never consulted when env is set


def test_falls_back_to_ssm_and_secrets_when_env_missing():
    env = {"AUTH_DISABLED": "1"}
    ssm_values = {"/marketplace-listing/s3_bucket": "bkt",
                  "/marketplace-listing/s3_region": "ap-south-1"}
    s = load_settings(
        env=env,
        ssm=lambda name: ssm_values.get(name),
        secrets=lambda name: "the-client-secret",
    )
    assert s.s3_bucket == "bkt"
    assert s.cognito_client_secret == "the-client-secret"


def test_ledger_store_local_when_path_set(tmp_path):
    env = {"AUTH_DISABLED": "1", "LEDGER_LOCAL_PATH": str(tmp_path / "led.json")}
    s = load_settings(env=env, ssm=lambda n: None, secrets=lambda n: None)
    store = ledger_store(s)
    assert isinstance(store, LocalJsonStore)
    assert store.get_json("anything") is None
    store.put_json("state/myntra_groupid.json", {"next_style_group_id": 5})
    assert store.get_json("state/myntra_groupid.json")["next_style_group_id"] == 5
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/web/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.web.settings'`.

- [ ] **Step 4: Write the implementation**

Create `src/web/__init__.py` (empty). Create `src/web/settings.py`:

```python
import json
import os
from dataclasses import dataclass

SSM_PREFIX = "/marketplace-listing/"
LEDGER_KEY = "state/myntra_groupid.json"

# env var name -> (settings attr, ssm param leaf, is_secret)
_FIELDS = [
    ("S3_BUCKET", "s3_bucket", "s3_bucket", False),
    ("S3_REGION", "s3_region", "s3_region", False),
    ("S3_PREFIX", "s3_prefix", "s3_prefix", False),
    ("COGNITO_POOL_ID", "cognito_pool_id", "cognito_pool_id", False),
    ("COGNITO_CLIENT_ID", "cognito_client_id", "cognito_client_id", False),
    ("COGNITO_DOMAIN", "cognito_domain", "cognito_domain", False),
    ("COGNITO_REDIRECT_URI", "cognito_redirect_uri", "cognito_redirect_uri", False),
    ("COGNITO_CLIENT_SECRET", "cognito_client_secret", "cognito_client_secret", True),
]


@dataclass
class Settings:
    s3_bucket: str = ""
    s3_region: str = "ap-south-1"
    s3_prefix: str = "myntra/"
    cognito_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_client_secret: str = ""
    cognito_domain: str = ""
    cognito_redirect_uri: str = ""
    auth_disabled: bool = False
    ledger_local_path: str | None = None


def _ssm_getter():
    import boto3
    client = boto3.client("ssm")

    def get(name):
        try:
            r = client.get_parameter(Name=name, WithDecryption=True)
            return r["Parameter"]["Value"]
        except client.exceptions.ParameterNotFound:
            return None
    return get


def _secrets_getter():
    import boto3
    client = boto3.client("secretsmanager")

    def get(name):
        try:
            return client.get_secret_value(SecretId=name)["SecretString"]
        except client.exceptions.ResourceNotFoundException:
            return None
    return get


def load_settings(env=None, ssm=None, secrets=None) -> Settings:
    """Resolve each value from env first, else from SSM/Secrets. Pass ssm/secrets
    callables in tests; in production they default to real AWS getters (lazy)."""
    env = os.environ if env is None else env
    s = Settings()
    s.auth_disabled = env.get("AUTH_DISABLED", "") in ("1", "true", "True")
    s.ledger_local_path = env.get("LEDGER_LOCAL_PATH") or None

    ssm = ssm if ssm is not None else (_ssm_getter() if not _all_env(env) else (lambda n: None))
    secrets = secrets if secrets is not None else (_secrets_getter() if not _all_env(env) else (lambda n: None))

    for env_name, attr, leaf, is_secret in _FIELDS:
        val = env.get(env_name)
        if val is None:
            val = (secrets if is_secret else ssm)(SSM_PREFIX + leaf if not is_secret else SSM_PREFIX + leaf)
        if val is not None:
            setattr(s, attr, val)
    return s


def _all_env(env):
    """True if every non-secret required field is present in env (so we can skip AWS)."""
    return all(env.get(n) for n, _, _, secret in _FIELDS if not secret)


class LocalJsonStore:
    """Dev/offline ledger store: a single JSON file on disk."""
    def __init__(self, path):
        self.path = path

    def get_json(self, key):
        if not os.path.exists(self.path):
            return None
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)

    def put_json(self, key, data):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)


def ledger_store(settings: Settings):
    if settings.ledger_local_path:
        return LocalJsonStore(settings.ledger_local_path)
    import boto3
    from src.myntra.groupid_ledger import S3JsonStore
    return S3JsonStore(settings.s3_bucket, boto3.client("s3", region_name=settings.s3_region))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_settings.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add requirements.txt src/web/__init__.py src/web/settings.py tests/web/__init__.py tests/web/test_settings.py
git commit -m "feat(web): layered settings loader (env -> SSM/Secrets) + ledger store factory"
```

---

## Task 2: Auth — dev bypass + Cognito JWT validation

**Files:**
- Create: `src/web/auth.py`
- Test: `tests/web/test_auth.py`

**Interfaces:**
- Consumes: `Settings` from Task 1.
- Produces:
  - `class User` (dataclass: `email: str`).
  - `def current_user(settings, token: str | None) -> User` — returns a fixed dev user when `settings.auth_disabled`, else validates the JWT and returns the user; raises `AuthError` on failure.
  - `class AuthError(Exception)`.
  - `def verify_jwt(token, settings, jwks) -> dict` — pure function verifying a Cognito access/id token against a JWKS dict (claims returned).

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_auth.py`:

```python
import pytest

from src.web.auth import current_user, AuthError, User
from src.web.settings import Settings


def test_dev_bypass_returns_fixed_user():
    s = Settings(auth_disabled=True)
    u = current_user(s, token=None)
    assert isinstance(u, User)
    assert u.email == "dev@local"


def test_missing_token_rejected_when_auth_on():
    s = Settings(auth_disabled=False, cognito_pool_id="p", cognito_client_id="c",
                 s3_region="ap-south-1")
    with pytest.raises(AuthError):
        current_user(s, token=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.web.auth'`.

- [ ] **Step 3: Write the implementation**

Create `src/web/auth.py`:

```python
from dataclasses import dataclass

from jose import jwt
from jose.utils import base64url_decode  # noqa: F401  (ensures crypto backend present)

_JWKS_CACHE = {}


class AuthError(Exception):
    pass


@dataclass
class User:
    email: str


def _jwks_url(settings):
    return (f"https://cognito-idp.{settings.s3_region}.amazonaws.com/"
            f"{settings.cognito_pool_id}/.well-known/jwks.json")


def _get_jwks(settings):
    url = _jwks_url(settings)
    if url not in _JWKS_CACHE:
        import urllib.request
        import json
        with urllib.request.urlopen(url, timeout=5) as r:
            _JWKS_CACHE[url] = json.loads(r.read())
    return _JWKS_CACHE[url]


def verify_jwt(token, settings, jwks):
    """Verify a Cognito JWT against a JWKS dict. Returns claims or raises AuthError."""
    try:
        headers = jwt.get_unverified_header(token)
        key = next((k for k in jwks["keys"] if k["kid"] == headers["kid"]), None)
        if key is None:
            raise AuthError("unknown signing key")
        claims = jwt.decode(
            token, key, algorithms=["RS256"],
            audience=settings.cognito_client_id,
            issuer=(f"https://cognito-idp.{settings.s3_region}.amazonaws.com/"
                    f"{settings.cognito_pool_id}"),
        )
        return claims
    except AuthError:
        raise
    except Exception as exc:  # jose raises various subclasses
        raise AuthError(str(exc))


def current_user(settings, token):
    if settings.auth_disabled:
        return User(email="dev@local")
    if not token:
        raise AuthError("no token")
    claims = verify_jwt(token, settings, _get_jwks(settings))
    return User(email=claims.get("email") or claims.get("username") or "unknown")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_auth.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/web/auth.py tests/web/test_auth.py
git commit -m "feat(web): auth dependency with AUTH_DISABLED dev bypass + Cognito JWT verify"
```

---

## Task 3: In-memory job store

**Files:**
- Create: `src/web/jobs.py`
- Test: `tests/web/test_jobs.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class Job` (dataclass: `id:str, status:str, steps:list[dict], result:dict|None, error:str|None, batch_id:str|None, range:list|None`). `status` in `{"running","done","error"}`.
  - `class JobStore`: `create() -> Job`, `get(job_id) -> Job|None`, `set_step(job_id, name, state, count=None)`, `finish(job_id, result)`, `fail(job_id, error)`.
  - Module-level `STEPS = ["Ingest CSV","Map attributes","Images → S3","Fill & validate","Ready"]`.
  - A module-level singleton `store = JobStore()`.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_jobs.py`:

```python
from src.web.jobs import JobStore, STEPS


def test_job_lifecycle():
    st = JobStore()
    job = st.create()
    assert job.status == "running"
    assert [s["name"] for s in job.steps] == STEPS
    assert all(s["state"] == "pending" for s in job.steps)

    st.set_step(job.id, "Ingest CSV", "done", count=7)
    fetched = st.get(job.id)
    ingest = next(s for s in fetched.steps if s["name"] == "Ingest CSV")
    assert ingest["state"] == "done"
    assert ingest["count"] == 7

    st.finish(job.id, {"filled": "out.xlsx", "products": 7})
    assert st.get(job.id).status == "done"
    assert st.get(job.id).result["products"] == 7


def test_fail_records_error():
    st = JobStore()
    job = st.create()
    st.fail(job.id, "boom")
    assert st.get(job.id).status == "error"
    assert st.get(job.id).error == "boom"


def test_get_unknown_returns_none():
    assert JobStore().get("nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_jobs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.web.jobs'`.

- [ ] **Step 3: Write the implementation**

Create `src/web/jobs.py`:

```python
import threading
import uuid
from dataclasses import dataclass, field

STEPS = ["Ingest CSV", "Map attributes", "Images → S3", "Fill & validate", "Ready"]


@dataclass
class Job:
    id: str
    status: str = "running"
    steps: list = field(default_factory=lambda: [
        {"name": n, "state": "pending", "count": None} for n in STEPS])
    result: dict | None = None
    error: str | None = None
    batch_id: str | None = None
    range: list | None = None


class JobStore:
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def create(self):
        job = Job(id=uuid.uuid4().hex)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id):
        return self._jobs.get(job_id)

    def set_step(self, job_id, name, state, count=None):
        with self._lock:
            job = self._jobs[job_id]
            for s in job.steps:
                if s["name"] == name:
                    s["state"] = state
                    if count is not None:
                        s["count"] = count

    def finish(self, job_id, result):
        with self._lock:
            job = self._jobs[job_id]
            job.result = result
            job.status = "done"

    def fail(self, job_id, error):
        with self._lock:
            job = self._jobs[job_id]
            job.error = error
            job.status = "error"


store = JobStore()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_jobs.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/web/jobs.py tests/web/test_jobs.py
git commit -m "feat(web): in-memory job store with step tracking"
```

---

## Task 4: App factory, pages router, base layout + static assets

**Files:**
- Create: `src/web/main.py`
- Create: `src/web/routers/__init__.py` (empty)
- Create: `src/web/routers/pages.py`
- Create: `src/web/templates/base.html`, `src/web/templates/home.html`
- Create: `src/web/static/app.css`, `src/web/static/htmx.min.js`
- Create: `src/web/runtime/.gitkeep`
- Modify: `.gitignore`
- Test: `tests/web/test_pages.py`

**Interfaces:**
- Consumes: `load_settings` (Task 1), `current_user`/`AuthError` (Task 2).
- Produces:
  - `def create_app(settings=None) -> FastAPI` — app factory; stores settings on `app.state.settings`, mounts `/static`, configures Jinja templates, includes routers, and registers an `AuthError` handler that redirects to login (or returns 401 when `auth_disabled` is false and no Cognito configured).
  - module-level `app = create_app()` for `uvicorn src.web.main:app`.
  - `def get_settings(request) -> Settings` and `def get_user(request) -> User` dependency helpers in `pages.py` (reused by later routers). `get_user` reads the bearer/cookie token and calls `current_user`.
  - `templates` Jinja2Templates instance exported from `main.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_pages.py`:

```python
from fastapi.testclient import TestClient

from src.web.main import create_app
from src.web.settings import Settings


def _client():
    return TestClient(create_app(Settings(auth_disabled=True, s3_bucket="b")))


def test_home_page_renders():
    r = _client().get("/")
    assert r.status_code == 200
    assert "Generate" in r.text
    assert "Fix" in r.text


def test_static_css_served():
    r = _client().get("/static/app.css")
    assert r.status_code == 200
    assert "#E8A33D" in r.text  # marigold accent present
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_pages.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.web.main'`.

- [ ] **Step 3: Create static assets**

Create `src/web/static/htmx.min.js` — download the vendored library:

```bash
curl -L -o src/web/static/htmx.min.js https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js
```
(If offline, create the file with the htmx 1.9.x minified source pasted in; the only requirement is a working htmx build at this path.)

Create `src/web/static/app.css` (Marigold Ops; port the look from `mockups/05-marigold-ops.html` and `mockups/07-marigold-home.html`). Minimum content the tests and templates rely on:

```css
:root{
  --bg:#191613; --panel:#221E1A; --line:#3a342c; --ink:#efe7d8; --mut:#8a8276;
  --marigold:#E8A33D; --green:#7BB87A;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:'Inter',system-ui,sans-serif;line-height:1.5}
a{color:var(--marigold);text-decoration:none}
.wrap{max-width:1000px;margin:0 auto;padding:24px}
.nav{display:flex;gap:18px;align-items:center;padding:14px 24px;
  border-bottom:1px solid var(--line);background:var(--panel)}
.nav .brand{font-family:'Space Grotesk',sans-serif;font-weight:600;color:var(--marigold)}
.nav a{color:var(--ink)}
h1,h2,h3{font-family:'Space Grotesk',sans-serif}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;margin:16px 0}
.btn{background:var(--marigold);color:var(--bg);border:none;border-radius:8px;
  padding:10px 16px;font-weight:600;cursor:pointer;font-size:14px}
.btn.green{background:var(--green)}
.drop{border:2px dashed var(--line);border-radius:10px;padding:30px;text-align:center;color:var(--mut)}
.mono{font-family:'IBM Plex Mono',monospace}
.stepper{list-style:none;padding:0;margin:0}
.stepper li{display:flex;gap:10px;align-items:center;padding:10px 0;border-bottom:1px dashed var(--line);color:var(--mut)}
.stepper li.done{color:var(--green)} .stepper li.active{color:var(--marigold)}
.flag{color:var(--marigold)} .ok{color:var(--green)}
.card{border:1px solid var(--line);border-radius:8px;padding:12px;margin:8px 0}
.card.auto{border-color:var(--green)} .card.need{border-color:var(--marigold)} .card.expl{border-color:var(--mut)}
input[type=text]{background:var(--bg);border:1px solid var(--marigold);color:var(--ink);
  border-radius:6px;padding:8px;font-family:'IBM Plex Mono',monospace;width:100%}
.hint{font-size:12px;color:var(--mut);margin-top:4px}
```

Create `src/web/runtime/.gitkeep` (empty). Append to `.gitignore`:

```
src/web/runtime/*
!src/web/runtime/.gitkeep
```

- [ ] **Step 4: Create templates**

Create `src/web/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{% block title %}Marigold Ops{% endblock %}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600&family=IBM+Plex+Mono&family=Inter&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/app.css">
  <script src="/static/htmx.min.js"></script>
</head>
<body>
  <nav class="nav">
    <span class="brand">Marigold Ops</span>
    <a href="/">Home</a><a href="/generate">Generate</a><a href="/fix">Fix errors</a>
    <span style="margin-left:auto" class="mono">{{ user.email if user else "" }}</span>
  </nav>
  <div class="wrap">{% block content %}{% endblock %}</div>
</body>
</html>
```

Create `src/web/templates/home.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>Myntra bulk listing</h1>
<p>Two things this app does, no code required:</p>
<div class="panel">
  <h3>Generate a Myntra sheet</h3>
  <p>Upload your Shopify <span class="mono">products_export.csv</span> and download a ready-to-upload Myntra file.</p>
  <a class="btn" href="/generate">Generate →</a>
</div>
<div class="panel">
  <h3>Fix rejected rows</h3>
  <p>Upload the rejection file Myntra sent back and get plain-language fixes.</p>
  <a class="btn" href="/fix">Fix errors →</a>
</div>
{% endblock %}
```

- [ ] **Step 5: Write the app factory + pages router**

Create `src/web/routers/__init__.py` (empty). Create `src/web/main.py`:

```python
import os

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.web.auth import AuthError
from src.web.settings import load_settings

_HERE = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(_HERE, "templates"))


def create_app(settings=None) -> FastAPI:
    app = FastAPI(title="Marigold Ops")
    app.state.settings = settings or load_settings()
    app.mount("/static", StaticFiles(directory=os.path.join(_HERE, "static")), name="static")

    from src.web.routers import pages, generate, fix
    app.include_router(pages.router)
    app.include_router(generate.router)
    app.include_router(fix.router)

    @app.exception_handler(AuthError)
    async def _auth_handler(request: Request, exc: AuthError):
        return JSONResponse({"detail": "login required"}, status_code=401)

    return app


app = create_app()
```

Create `src/web/routers/pages.py`:

```python
from fastapi import APIRouter, Request

from src.web.auth import current_user

router = APIRouter()


def get_settings(request: Request):
    return request.app.state.settings


def get_user(request: Request):
    settings = request.app.state.settings
    token = (request.cookies.get("id_token")
             or (request.headers.get("authorization", "").removeprefix("Bearer ").strip() or None))
    return current_user(settings, token)


@router.get("/")
def home(request: Request):
    from src.web.main import templates
    user = get_user(request)
    return templates.TemplateResponse("home.html", {"request": request, "user": user})
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_pages.py -v`
Expected: PASS (2 tests). Also run the full suite to confirm nothing else broke: `python -m pytest -q` → all green.

- [ ] **Step 7: Commit**

```bash
git add src/web/main.py src/web/routers/__init__.py src/web/routers/pages.py src/web/templates/base.html src/web/templates/home.html src/web/static/app.css src/web/static/htmx.min.js src/web/runtime/.gitkeep .gitignore tests/web/test_pages.py
git commit -m "feat(web): app factory, home page, Marigold Ops static assets"
```

---

## Task 5: Flow A — Generate (upload → background job → result → confirm)

**Files:**
- Create: `src/web/routers/generate.py`
- Create: `src/web/templates/generate.html`, `_stepper.html`, `_result.html`
- Test: `tests/web/test_generate.py`

**Interfaces:**
- Consumes: `pipeline.main` (override `style_group_id_start`, `out_dir`, `csv_path`), `groupid_ledger.reserve/confirm`, `ledger_store` (Task 1), `jobs.store` (Task 3), `get_user`/`get_settings` (Task 4).
- Produces routes:
  - `GET /generate` → upload form (shows ledger's next id).
  - `POST /generate` (multipart `file`) → saves CSV to a job temp dir, reserves a batch, starts a background task, returns the progress fragment with htmx polling.
  - `GET /jobs/{job_id}` → `_stepper.html` while running, `_result.html` when done.
  - `GET /generate/download/{job_id}` → streams the filled xlsx.
  - `POST /generate/confirm/{job_id}` → calls `confirm(batch_id)`; returns confirmation text.
  - `def _run_generate(job_id, csv_path, out_dir, start, settings)` — the background worker (testable directly).

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_generate.py`:

```python
import io
from unittest import mock

from fastapi.testclient import TestClient

from src.web.main import create_app
from src.web.settings import Settings
import src.web.routers.generate as gen


def _client(tmp_path):
    s = Settings(auth_disabled=True, s3_bucket="b",
                 ledger_local_path=str(tmp_path / "led.json"))
    return TestClient(create_app(s)), s


def test_generate_rejects_non_csv(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/generate", files={"file": ("notes.txt", b"hi", "text/plain")})
    assert r.status_code == 400


def test_generate_runs_job_and_confirm_advances_ledger(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)

    # Stub the heavy pipeline: pretend it wrote a file for 3 products.
    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None, **kw):
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"xlsx-bytes")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("3 rows\n1 vocab flag: Ivory\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx",
                "report": f"{out_dir}/report.txt", "products": 3, "uploaded": 9}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)
    # count products from CSV deterministically (3 data rows)
    monkeypatch.setattr(gen, "count_products", lambda path: 3)

    csv = b"Handle,Title\na,A\nb,B\nc,C\n"
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    assert r.status_code == 200
    job_id = r.headers["x-job-id"]

    # Background task runs inline under TestClient; poll once.
    poll = client.get(f"/jobs/{job_id}")
    assert poll.status_code == 200
    assert "Download" in poll.text
    assert "16" in poll.text or "1 –" in poll.text or "1 - 3" in poll.text  # range shown

    # ledger started empty (next id 1) -> reserve was [1,3]; confirm advances to 4
    rc = client.post(f"/generate/confirm/{job_id}")
    assert rc.status_code == 200
    from src.myntra.groupid_ledger import read_ledger
    from src.web.settings import ledger_store
    led = read_ledger(ledger_store(settings))
    assert led["next_style_group_id"] == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_generate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.web.routers.generate'`.

- [ ] **Step 3: Write the templates**

Create `src/web/templates/generate.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>Generate Myntra sheet</h1>
<form class="panel" hx-post="/generate" hx-encoding="multipart/form-data"
      hx-target="#progress" hx-swap="innerHTML">
  <div class="drop">⬆ Choose your <span class="mono">products_export.csv</span>
    <input type="file" name="file" accept=".csv" required></div>
  <p class="mono">styleGroupId start: <strong>{{ next_id }}</strong> (auto, from ledger)</p>
  <button class="btn" type="submit">Generate →</button>
</form>
<div id="progress"></div>
{% endblock %}
```

Create `src/web/templates/_stepper.html`:

```html
<div hx-get="/jobs/{{ job.id }}" hx-trigger="load delay:1s" hx-swap="outerHTML">
  <h3>Working… {{ count }} products</h3>
  <ul class="stepper">
    {% for s in job.steps %}
    <li class="{{ 'done' if s.state=='done' else 'active' if s.state=='active' else '' }}">
      {{ '✓' if s.state=='done' else '●' if s.state=='active' else '○' }} {{ s.name }}
      {% if s.count is not none %}<span style="margin-left:auto" class="mono">{{ s.count }}</span>{% endif %}
    </li>
    {% endfor %}
  </ul>
</div>
```

Create `src/web/templates/_result.html`:

```html
<div class="panel">
  {% if job.status == 'error' %}
    <h3 class="flag">⚠ Generation failed</h3>
    <pre class="mono">{{ job.error }}</pre>
  {% else %}
    <h3 class="ok">✅ Sheet ready — {{ job.result.products }} SKUs</h3>
    <p class="mono">{{ job.result.uploaded }} images uploaded</p>
    <pre class="mono">{{ report }}</pre>
    <p class="flag mono">styleGroupId range assigned: {{ job.range[0] }} – {{ job.range[1] }}</p>
    <a class="btn" href="/generate/download/{{ job.id }}">⬇ Download xlsx</a>
    <div style="margin-top:14px">
      <button class="btn green" hx-post="/generate/confirm/{{ job.id }}"
              hx-swap="outerHTML">✓ Mark upload successful</button>
    </div>
  {% endif %}
</div>
```

- [ ] **Step 4: Write the router**

Create `src/web/routers/generate.py`:

```python
import csv as csvmod
import os
import shutil

from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from src.myntra.groupid_ledger import reserve, confirm
from src.myntra.pipeline import main as pipeline_main  # noqa: F401 (patched in tests)
from src.web.jobs import store
from src.web.routers.pages import get_user, get_settings
from src.web.settings import ledger_store

router = APIRouter()
RUNTIME = os.path.join(os.path.dirname(os.path.dirname(__file__)), "runtime")


def count_products(path):
    """Number of distinct Shopify products = rows with a non-empty Handle (header excluded)."""
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csvmod.DictReader(fh)
        handles = {r.get("Handle") for r in reader if r.get("Handle")}
    return len(handles) or 1


def _templates():
    from src.web.main import templates
    return templates


@router.get("/generate", response_class=HTMLResponse)
def generate_form(request: Request):
    get_user(request)
    settings = get_settings(request)
    from src.myntra.groupid_ledger import read_ledger
    next_id = read_ledger(ledger_store(settings))["next_style_group_id"]
    return _templates().TemplateResponse(
        "generate.html", {"request": request, "user": get_user(request), "next_id": next_id})


@router.post("/generate", response_class=HTMLResponse)
def generate_submit(request: Request, file: UploadFile = File(...)):
    get_user(request)
    settings = get_settings(request)
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file")

    job = store.create()
    job_dir = os.path.join(RUNTIME, job.id)
    os.makedirs(job_dir, exist_ok=True)
    csv_path = os.path.join(job_dir, "products_export.csv")
    with open(csv_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    count = count_products(csv_path)
    start, batch_id = reserve(ledger_store(settings), count, "myntra_filled.xlsx")
    job.batch_id = batch_id
    job.range = [start, start + count - 1]

    _spawn(job.id, csv_path, job_dir, start, settings)

    resp = _templates().TemplateResponse(
        "_stepper.html", {"request": request, "job": job, "count": count})
    resp.headers["x-job-id"] = job.id
    return resp


def _spawn(job_id, csv_path, job_dir, start, settings):
    import threading
    threading.Thread(target=_run_generate,
                     args=(job_id, csv_path, job_dir, start, settings), daemon=True).start()


def _run_generate(job_id, csv_path, job_dir, start, settings):
    try:
        store.set_step(job_id, "Ingest CSV", "active")
        res = pipeline_main(csv_path=csv_path, out_dir=job_dir, style_group_id_start=start)
        for name in ["Ingest CSV", "Map attributes", "Images → S3", "Fill & validate", "Ready"]:
            store.set_step(job_id, name, "done")
        store.set_step(job_id, "Images → S3", "done", count=res.get("uploaded"))
        store.finish(job_id, res)
    except Exception as exc:  # surface failure to the UI
        store.fail(job_id, f"{type(exc).__name__}: {exc}")


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_status(request: Request, job_id: str):
    get_user(request)
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    if job.status == "running":
        count = sum(1 for _ in [s for s in job.steps if s["state"] == "done"]) or ""
        return _templates().TemplateResponse(
            "_stepper.html", {"request": request, "job": job, "count": count})
    report = ""
    if job.result and os.path.exists(job.result.get("report", "")):
        with open(job.result["report"], encoding="utf-8") as fh:
            report = fh.read()
    return _templates().TemplateResponse(
        "_result.html", {"request": request, "job": job, "report": report})


@router.get("/generate/download/{job_id}")
def download(request: Request, job_id: str):
    get_user(request)
    job = store.get(job_id)
    if not job or not job.result:
        raise HTTPException(status_code=404, detail="not ready")
    return FileResponse(job.result["filled"], filename="myntra_filled.xlsx")


@router.post("/generate/confirm/{job_id}", response_class=HTMLResponse)
def confirm_upload(request: Request, job_id: str):
    get_user(request)
    settings = get_settings(request)
    job = store.get(job_id)
    if not job or not job.batch_id:
        raise HTTPException(status_code=404, detail="unknown job")
    new_next = confirm(ledger_store(settings), job.batch_id)
    return HTMLResponse(
        f'<p class="ok mono">✓ Confirmed. Ledger advanced to {new_next}.</p>')
```

> Note: `_spawn` launches `_run_generate` on a daemon thread. Under FastAPI's `TestClient` the thread runs and the single poll in the test sees `done` because `pipeline_main` is stubbed and returns immediately.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_generate.py -v`
Expected: PASS (2 tests). If the poll occasionally races the thread, add a short retry loop in the test polling `/jobs/{job_id}` up to 20×0.05s until `"Download"` appears — but with the stubbed pipeline it completes effectively instantly.

- [ ] **Step 6: Commit**

```bash
git add src/web/routers/generate.py src/web/templates/generate.html src/web/templates/_stepper.html src/web/templates/_result.html tests/web/test_generate.py
git commit -m "feat(web): Flow A generate — upload, background job, result, ledger confirm"
```

---

## Task 6: Flow B — Fix errors (upload → classify → typed fixes → regenerate)

**Files:**
- Create: `src/web/routers/fix.py`
- Create: `src/web/templates/fix.html`, `_fix_review.html`, `_fix_result.html`
- Test: `tests/web/test_fix.py`

**Interfaces:**
- Consumes: `error_reader.load_rules/read_errors`, `corrector.correct`, `template_reader.read_template`, `mapper.validate_value`, config files under `config/myntra/`, the Myntra template under `templates/myntra/`.
- Produces routes:
  - `GET /fix` → upload form.
  - `POST /fix` (multipart `file`) → parse + classify; render review buckets (auto/need/explain) with typed inputs and drop checkboxes. Stash parsed rows in a temp dir keyed by a `fix_id`.
  - `POST /fix/apply/{fix_id}` (form fields `answer__<sku>__<field>`, `drop__<sku>`) → run `correct`, render result + download link.
  - `GET /fix/download/{fix_id}` → stream corrected xlsx.
  - `def _resolve_template_path()` and `def _load_constants()` helpers.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_fix.py`:

```python
import os
from unittest import mock

from fastapi.testclient import TestClient

from src.web.main import create_app
from src.web.settings import Settings
import src.web.routers.fix as fixmod
from src.myntra.error_reader import RowError


def _client():
    return TestClient(create_app(Settings(auth_disabled=True, s3_bucket="b")))


def _fake_rows():
    return [
        RowError(row=4, sku="78SAZ", status="ERROR",
                 cells={"vendorSkuCode": "78SAZ", "Prominent Colour": "Ivory"},
                 issues=[{"category": "vocab", "action": "manual_choice",
                          "field": "Prominent Colour", "explanation": "Pick a Myntra colour",
                          "raw": "colour not in dropdown"}]),
        RowError(row=5, sku="81COT", status="ERROR",
                 cells={"vendorSkuCode": "81COT"},
                 issues=[{"category": "duplicate", "action": "drop_sku",
                          "field": None, "explanation": "Already listed",
                          "raw": "already registered"}]),
    ]


def test_fix_upload_shows_buckets(monkeypatch):
    client = _client()
    monkeypatch.setattr(fixmod, "read_errors", lambda path, rules: _fake_rows())
    monkeypatch.setattr(fixmod, "load_rules", lambda: {"rules": [], "unknown": {}})
    r = client.post("/fix", files={"file": ("rej.xlsx", b"x", "application/vnd.ms-excel")})
    assert r.status_code == 200
    assert "needs you" in r.text.lower()
    assert "Prominent Colour" in r.text
    assert "Drop this SKU" in r.text


def test_fix_apply_calls_correct_with_typed_answer(monkeypatch, tmp_path):
    client = _client()
    rows = _fake_rows()
    monkeypatch.setattr(fixmod, "read_errors", lambda path, rules: rows)
    monkeypatch.setattr(fixmod, "load_rules", lambda: {"rules": [], "unknown": {}})
    monkeypatch.setattr(fixmod, "read_template", lambda p: object())
    monkeypatch.setattr(fixmod, "_load_constants", lambda: {})
    monkeypatch.setattr(fixmod, "_resolve_template_path", lambda: "tpl.xlsx")

    captured = {}

    def fake_correct(row_errors, template, template_path, constants, answers, drops, out_path):
        captured["answers"] = answers
        captured["drops"] = drops
        with open(out_path, "wb") as fh:
            fh.write(b"corrected")
        return {"written": 1, "dropped": list(drops), "changed": {"78SAZ": ["Prominent Colour"]},
                "rejected": {}}

    monkeypatch.setattr(fixmod, "correct", fake_correct)

    # first upload to create the fix_id + cached rows
    up = client.post("/fix", files={"file": ("rej.xlsx", b"x", "application/vnd.ms-excel")})
    fix_id = up.headers["x-fix-id"]

    r = client.post(f"/fix/apply/{fix_id}", data={
        "answer__78SAZ__Prominent Colour": "Off White",
        "drop__81COT": "on",
    })
    assert r.status_code == 200
    assert captured["answers"] == {"78SAZ": {"Prominent Colour": "Off White"}}
    assert captured["drops"] == {"81COT"}
    assert "corrected" in r.text.lower() or "Download" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_fix.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.web.routers.fix'`.

- [ ] **Step 3: Write the templates**

Create `src/web/templates/fix.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>Fix Myntra errors</h1>
<form class="panel" hx-post="/fix" hx-encoding="multipart/form-data"
      hx-target="#review" hx-swap="innerHTML">
  <div class="drop">⬆ Drop the rejection <span class="mono">.xlsx</span> Myntra sent back
    <input type="file" name="file" accept=".xlsx" required></div>
  <button class="btn" type="submit">Check errors →</button>
</form>
<div id="review"></div>
{% endblock %}
```

Create `src/web/templates/_fix_review.html`:

```html
<form hx-post="/fix/apply/{{ fix_id }}" hx-target="#review" hx-swap="innerHTML">
  {% for r in rows %}
    {% for issue in r.issues %}
      {% if issue.action == 'auto_fix' %}
        <div class="card auto"><strong class="mono">{{ r.sku }}</strong>
          <span class="ok">auto-fixed</span><div>{{ issue.explanation }}</div></div>
      {% elif issue.action == 'manual_choice' %}
        <div class="card need"><strong class="mono">{{ r.sku }}</strong>
          <span class="flag">needs you</span>
          <div>{{ issue.explanation }} — field <strong>{{ issue.field }}</strong></div>
          <input type="text" name="answer__{{ r.sku }}__{{ issue.field }}"
                 value="{{ r.cells.get(issue.field, '') }}">
          <div class="hint">Type the correct Myntra value. It is checked before writing.</div></div>
      {% elif issue.action == 'drop_sku' %}
        <div class="card need"><strong class="mono">{{ r.sku }}</strong>
          <span class="flag">needs you</span><div>{{ issue.explanation }}</div>
          <label><input type="checkbox" name="drop__{{ r.sku }}"> Drop this SKU</label></div>
      {% else %}
        <div class="card expl"><strong class="mono">{{ r.sku }}</strong>
          <span>explain only</span><div>{{ issue.explanation }}</div>
          <div class="hint mono">raw: {{ issue.raw }}</div></div>
      {% endif %}
    {% endfor %}
  {% endfor %}
  <button class="btn" type="submit">Apply &amp; regenerate →</button>
</form>
```

Create `src/web/templates/_fix_result.html`:

```html
<div class="panel">
  <h3 class="ok">✅ Corrected sheet ready</h3>
  <ul class="mono">
    <li>{{ summary.written }} rows written</li>
    <li>{{ summary.dropped|length }} dropped: {{ summary.dropped|join(', ') }}</li>
    <li>{{ summary.changed|length }} changed</li>
    {% if summary.rejected %}<li class="flag">rejected (not valid Myntra values): {{ summary.rejected }}</li>{% endif %}
  </ul>
  <a class="btn" href="/fix/download/{{ fix_id }}">⬇ Download corrected xlsx</a>
</div>
```

- [ ] **Step 4: Write the router**

Create `src/web/routers/fix.py`:

```python
import os
import pickle
import shutil
import uuid

import yaml
from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from src.myntra.corrector import correct
from src.myntra.error_reader import load_rules, read_errors
from src.myntra.mapper import validate_value  # noqa: F401 (correct() uses it internally)
from src.myntra.template_reader import read_template
from src.web.routers.pages import get_user

router = APIRouter()
RUNTIME = os.path.join(os.path.dirname(os.path.dirname(__file__)), "runtime")
CONSTANTS = os.path.join("config", "myntra", "constants.yaml")
TEMPLATE = os.path.join("templates", "myntra", "Myntra-Sku-Template-2026-06-16.xlsx")


def _templates():
    from src.web.main import templates
    return templates


def _resolve_template_path():
    return TEMPLATE


def _load_constants():
    with open(CONSTANTS, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@router.get("/fix", response_class=HTMLResponse)
def fix_form(request: Request):
    get_user(request)
    return _templates().TemplateResponse("fix.html", {"request": request, "user": get_user(request)})


@router.post("/fix", response_class=HTMLResponse)
def fix_upload(request: Request, file: UploadFile = File(...)):
    get_user(request)
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Please upload the Myntra .xlsx file")
    fix_id = uuid.uuid4().hex
    fix_dir = os.path.join(RUNTIME, "fix-" + fix_id)
    os.makedirs(fix_dir, exist_ok=True)
    err_path = os.path.join(fix_dir, "rejection.xlsx")
    with open(err_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    rows = read_errors(err_path, load_rules())
    with open(os.path.join(fix_dir, "rows.pkl"), "wb") as fh:
        pickle.dump(rows, fh)

    resp = _templates().TemplateResponse(
        "_fix_review.html", {"request": request, "rows": rows, "fix_id": fix_id})
    resp.headers["x-fix-id"] = fix_id
    return resp


@router.post("/fix/apply/{fix_id}", response_class=HTMLResponse)
async def fix_apply(request: Request, fix_id: str):
    get_user(request)
    fix_dir = os.path.join(RUNTIME, "fix-" + fix_id)
    rows_pkl = os.path.join(fix_dir, "rows.pkl")
    if not os.path.exists(rows_pkl):
        raise HTTPException(status_code=404, detail="session expired, please re-upload")
    with open(rows_pkl, "rb") as fh:
        rows = pickle.load(fh)

    form = await request.form()
    answers, drops = {}, set()
    for key, value in form.items():
        if key.startswith("answer__") and str(value).strip():
            _, sku, field = key.split("__", 2)
            answers.setdefault(sku, {})[field] = value
        elif key.startswith("drop__"):
            drops.add(key.split("__", 1)[1])

    template = read_template(_resolve_template_path())
    out_path = os.path.join(fix_dir, "myntra_corrected.xlsx")
    summary = correct(rows, template, _resolve_template_path(), _load_constants(),
                      answers, drops, out_path)
    return _templates().TemplateResponse(
        "_fix_result.html", {"request": request, "summary": summary, "fix_id": fix_id})


@router.get("/fix/download/{fix_id}")
def fix_download(request: Request, fix_id: str):
    get_user(request)
    path = os.path.join(RUNTIME, "fix-" + fix_id, "myntra_corrected.xlsx")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="not ready")
    return FileResponse(path, filename="myntra_corrected.xlsx")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_fix.py -v`
Expected: PASS (2 tests). Then run the whole suite: `python -m pytest -q` → all green (existing 41 + new web tests).

- [ ] **Step 6: Commit**

```bash
git add src/web/routers/fix.py src/web/templates/fix.html src/web/templates/_fix_review.html src/web/templates/_fix_result.html tests/web/test_fix.py
git commit -m "feat(web): Flow B fix-errors — classify buckets, typed fixes, regenerate"
```

---

## Task 7: Dockerfile — serve the web app

**Files:**
- Modify: `Dockerfile`

**Interfaces:**
- Consumes: the whole `src/web` app.
- Produces: an image whose `CMD` runs uvicorn.

- [ ] **Step 1: Update the CMD**

Open `Dockerfile`. Replace the final `CMD ["python", "run.py"]` line with:

```dockerfile
EXPOSE 8080
CMD ["uvicorn", "src.web.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

Confirm `requirements.txt` (already updated in Task 1) is copied/installed before the source copy so the layer cache still works (no change needed if the existing Dockerfile already does deps-before-src).

- [ ] **Step 2: Build and smoke-test locally**

Run:
```bash
docker build -t marketplace-bulklisting:web .
docker run --rm -e AUTH_DISABLED=1 -e S3_BUCKET=dummy -e LEDGER_LOCAL_PATH=/tmp/led.json -p 8080:8080 marketplace-bulklisting:web &
sleep 4
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/
```
Expected: `200`. Then stop the container (`docker stop $(docker ps -q --filter ancestor=marketplace-bulklisting:web)`).

- [ ] **Step 3: Commit**

```bash
git add Dockerfile
git commit -m "feat(web): Dockerfile CMD runs uvicorn web server"
```

---

## Task 8: Cognito console setup runbook

**Files:**
- Create: `docs/runbooks/web-cognito-setup-console.md`

**Interfaces:**
- Consumes: account 048589483919, region ap-south-1 (from CI/CD memory).
- Produces: a click-through runbook + the env vars the app expects (`COGNITO_POOL_ID`, `COGNITO_CLIENT_ID`, `COGNITO_CLIENT_SECRET`, `COGNITO_DOMAIN`, `COGNITO_REDIRECT_URI`).

- [ ] **Step 1: Write the runbook**

Create `docs/runbooks/web-cognito-setup-console.md` in the same pre-filled, click-through style as `docs/runbooks/cicd-aws-setup-console.md`. It MUST contain, as numbered console steps with all values filled in:

1. Cognito → Create user pool (region ap-south-1); sign-in option = Email; name `marketplace-listing-pool`.
2. App client: create a **confidential** client `marketplace-listing-web` (generate client secret); enable Authorization code grant; callback URL `http://localhost:8000/auth/callback` (dev) — note prod URL added later; sign-out URL `http://localhost:8000/`.
3. Hosted UI domain: set a Cognito domain prefix `ijor-marketplace`.
4. Create a user: add `gopalthakur71@gmail.com` (or a teammate), set a password.
5. Collect values → set these env vars (or `.env`) for local run:
   `COGNITO_POOL_ID`, `COGNITO_CLIENT_ID`, `COGNITO_CLIENT_SECRET`, `COGNITO_DOMAIN`, `COGNITO_REDIRECT_URI`, and unset `AUTH_DISABLED` to enforce login.
6. Verify: run `uvicorn src.web.main:app`, open `/`, confirm redirect to the Cognito hosted login and back.
7. Teardown notes (delete pool/app client).

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/web-cognito-setup-console.md
git commit -m "docs(web): Cognito console setup runbook"
```

---

## Task 9: SSM + Secrets Manager console setup runbook

**Files:**
- Create: `docs/runbooks/web-ssm-secrets-setup-console.md`

**Interfaces:**
- Consumes: account 048589483919, region ap-south-1; the `SSM_PREFIX = "/marketplace-listing/"` from `settings.py`.
- Produces: a runbook to create the SSM parameters and the Secrets Manager secret the loader falls back to.

- [ ] **Step 1: Write the runbook**

Create `docs/runbooks/web-ssm-secrets-setup-console.md` in the pre-filled click-through style. It MUST contain:

1. Systems Manager → Parameter Store → create String parameters (Standard tier), names exactly:
   `/marketplace-listing/s3_bucket` = `ijorethnicpartners`,
   `/marketplace-listing/s3_region` = `ap-south-1`,
   `/marketplace-listing/s3_prefix` = `myntra/`,
   `/marketplace-listing/cognito_pool_id` = (from Task 8),
   `/marketplace-listing/cognito_client_id` = (from Task 8),
   `/marketplace-listing/cognito_domain` = (from Task 8),
   `/marketplace-listing/cognito_redirect_uri` = (prod callback URL).
2. Secrets Manager → store a new secret, plaintext, name `/marketplace-listing/cognito_client_secret`, value = the Cognito client secret.
3. Local verification: unset the corresponding env vars, keep AWS creds in your shell, run the app, confirm it boots reading from SSM/Secrets (loader falls back automatically). Note: the IAM permissions to *read* these (ssm:GetParameter*, secretsmanager:GetSecretValue) are added to the EC2 instance role in Phase 4; locally your existing IAM user can read them.
4. Teardown notes.

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/web-ssm-secrets-setup-console.md
git commit -m "docs(web): SSM + Secrets Manager console setup runbook"
```

---

## Self-review notes (for the implementer)

- After Task 6, run the **entire** suite (`python -m pytest -q`) and confirm the original 41 backend tests still pass — the web layer must not have touched backend logic.
- Spec coverage check: §3 architecture → Tasks 1–6; §4 settings/auth → Tasks 1–2; §5 Generate → Task 5; §6 Fix → Task 6; §7 jobs → Task 3; §8 styling → Task 4; §9 Dockerfile/build order → Task 7 + runbook tasks; §10 testing → tests in every task.
- The `_spawn` daemon-thread approach (Task 5) is deliberate so tests run without an event loop dependency. If you later move to FastAPI `BackgroundTasks`, keep `_run_generate` signature stable so its unit test is unaffected.
- Manual end-to-end check (after Task 6, optional): `LEDGER_LOCAL_PATH=/tmp/led.json AUTH_DISABLED=1 uvicorn src.web.main:app --reload`, then upload the real `products_export.csv` at `/generate` (set `upload=False` path via `image_specs.yaml` if you want to skip S3) and a captured file from `errors/myntra/` at `/fix`.
