# Stage 2 — Real Auth (Cognito hosted-UI login) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real Cognito hosted-UI login round-trip (`/login`, `/auth/callback`, `/logout`) so the Marigold Ops web app can run with `AUTH_DISABLED` off.

**Architecture:** The request-time auth path is unchanged — routes already call `get_user(request)` → `current_user` → `verify_jwt`. We add three OAuth endpoints plus a small pure `oauth.py` helper module (stdlib `urllib`, no new deps), set an `id_token` cookie on successful callback, and make the `AuthError` handler redirect browsers to `/login`. Sessions are re-login-on-stale (no refresh tokens).

**Tech Stack:** FastAPI, Starlette responses, `python-jose` (already present, used by `auth.py`), stdlib `urllib`/`base64`/`secrets`, pytest + `fastapi.testclient.TestClient`.

## Global Constraints

- **No new runtime dependencies** — use stdlib (`urllib`, `base64`, `secrets`, `json`) to match [src/web/auth.py](../../../src/web/auth.py).
- **Reuse `AuthError`** from `src.web.auth` for all auth failures (do not define a new exception).
- **Cookies:** `HttpOnly; SameSite=Lax; Path=/`; the `Secure` attribute is driven by `settings.cookie_secure` (default `False`, because there is no TLS yet).
- **Cognito facts (non-secret):** domain `ijor-marketplace`, region `ap-south-1`, pool `ap-south-1_NdxNQ1plz`, client `29oo5dtqh8j50k2481lmffqb0e`. Hosted-UI base is `https://{cognito_domain}.auth.{s3_region}.amazoncognito.com`. Scopes: `openid email`.
- **Test settings pattern:** construct `Settings(...)` directly and pass to `create_app(settings)`; use `TestClient(app, follow_redirects=False)` to assert redirects.

---

### Task 1: Add `cookie_secure` setting

**Files:**
- Modify: `src/web/settings.py` (the `Settings` dataclass + `load_settings`)
- Modify: `.env.example` (document the new key)
- Test: `tests/web/test_settings.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Settings.cookie_secure: bool` (default `False`), resolved from env `COOKIE_SECURE` (`"1"/"true"/"True"`). Not an SSM field.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_settings.py`:

```python
def test_cookie_secure_from_env():
    s = load_settings(env={"AUTH_DISABLED": "1", "COOKIE_SECURE": "1"},
                      ssm=lambda n: None, secrets=lambda n: None)
    assert s.cookie_secure is True


def test_cookie_secure_defaults_off():
    s = load_settings(env={"AUTH_DISABLED": "1"},
                      ssm=lambda n: None, secrets=lambda n: None)
    assert s.cookie_secure is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/web/test_settings.py -k cookie_secure -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'cookie_secure'`.

- [ ] **Step 3: Implement the setting**

In `src/web/settings.py`, add the field to the `Settings` dataclass (next to `auth_disabled`):

```python
    auth_disabled: bool = False
    cookie_secure: bool = False
    ledger_local_path: str | None = None
```

In `load_settings`, right after the `auth_disabled` line:

```python
    s.auth_disabled = env.get("AUTH_DISABLED", "") in ("1", "true", "True")
    s.cookie_secure = env.get("COOKIE_SECURE", "") in ("1", "true", "True")
```

- [ ] **Step 4: Document the key**

Add to `.env.example` (near the other web/app toggles):

```
# Send the id_token cookie with the Secure attribute (requires HTTPS). Off by default.
COOKIE_SECURE=0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_settings.py -v`
Expected: PASS (all, including the two new tests).

- [ ] **Step 6: Commit**

```bash
git add src/web/settings.py .env.example tests/web/test_settings.py
git commit -m "feat(web): add COOKIE_SECURE setting for the auth cookie"
```

---

### Task 2: `src/web/oauth.py` — pure OAuth helpers

**Files:**
- Create: `src/web/oauth.py`
- Test: `tests/web/test_oauth.py`

**Interfaces:**
- Consumes: `Settings` fields `cognito_domain`, `s3_region`, `cognito_client_id`, `cognito_client_secret`, `cognito_redirect_uri`; `AuthError` from `src.web.auth`.
- Produces:
  - `authorize_url(settings, state) -> str`
  - `exchange_code(settings, code, http=_post) -> dict` (returns token JSON containing `id_token`; raises `AuthError` on failure/missing `id_token`)
  - `logout_url(settings) -> str`
  - `_post(url, data, headers) -> dict` (default injectable HTTP helper)

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_oauth.py`:

```python
import pytest

from src.web.auth import AuthError
from src.web.oauth import authorize_url, exchange_code, logout_url
from src.web.settings import Settings

S = Settings(
    cognito_domain="ijor-marketplace",
    s3_region="ap-south-1",
    cognito_client_id="cid",
    cognito_client_secret="sec",
    cognito_redirect_uri="http://localhost:8000/auth/callback",
)


def test_authorize_url_has_expected_params():
    url = authorize_url(S, "xyz")
    assert url.startswith(
        "https://ijor-marketplace.auth.ap-south-1.amazoncognito.com/oauth2/authorize?")
    assert "response_type=code" in url
    assert "client_id=cid" in url
    assert "state=xyz" in url
    assert "scope=openid+email" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fauth%2Fcallback" in url


def test_exchange_code_sends_basic_auth_and_parses_tokens():
    captured = {}

    def fake_http(url, data, headers):
        captured.update(url=url, data=data, headers=headers)
        return {"id_token": "tok", "access_token": "a", "refresh_token": "r"}

    tokens = exchange_code(S, "the-code", http=fake_http)
    assert tokens["id_token"] == "tok"
    assert captured["url"].endswith("/oauth2/token")
    assert captured["data"]["grant_type"] == "authorization_code"
    assert captured["data"]["code"] == "the-code"
    assert captured["data"]["redirect_uri"] == S.cognito_redirect_uri
    assert captured["headers"]["Authorization"].startswith("Basic ")


def test_exchange_code_raises_without_id_token():
    with pytest.raises(AuthError):
        exchange_code(S, "c", http=lambda url, data, headers: {"error": "invalid_grant"})


def test_exchange_code_wraps_http_errors():
    def boom(url, data, headers):
        raise RuntimeError("500 from cognito")

    with pytest.raises(AuthError):
        exchange_code(S, "c", http=boom)


def test_logout_url_points_to_site_root():
    url = logout_url(S)
    assert url.startswith(
        "https://ijor-marketplace.auth.ap-south-1.amazoncognito.com/logout?")
    assert "client_id=cid" in url
    assert "logout_uri=http%3A%2F%2Flocalhost%3A8000%2F" in url
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/web/test_oauth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.web.oauth'`.

- [ ] **Step 3: Implement `src/web/oauth.py`**

```python
import base64
import json
import urllib.parse
import urllib.request

from src.web.auth import AuthError


def _base(settings):
    return (f"https://{settings.cognito_domain}.auth."
            f"{settings.s3_region}.amazoncognito.com")


def authorize_url(settings, state):
    q = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": settings.cognito_client_id,
        "redirect_uri": settings.cognito_redirect_uri,
        "scope": "openid email",
        "state": state,
    })
    return f"{_base(settings)}/oauth2/authorize?{q}"


def logout_url(settings):
    logout_uri = urllib.parse.urljoin(settings.cognito_redirect_uri, "/")
    q = urllib.parse.urlencode({
        "client_id": settings.cognito_client_id,
        "logout_uri": logout_uri,
    })
    return f"{_base(settings)}/logout?{q}"


def _post(url, data, headers):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def exchange_code(settings, code, http=_post):
    basic = base64.b64encode(
        f"{settings.cognito_client_id}:{settings.cognito_client_secret}".encode()
    ).decode()
    try:
        tokens = http(
            f"{_base(settings)}/oauth2/token",
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.cognito_redirect_uri,
            },
            {
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    except Exception as exc:
        raise AuthError(f"token exchange failed: {exc}")
    if not isinstance(tokens, dict) or "id_token" not in tokens:
        raise AuthError("token response missing id_token")
    return tokens
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_oauth.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/web/oauth.py tests/web/test_oauth.py
git commit -m "feat(web): add oauth helpers (authorize/exchange/logout urls)"
```

---

### Task 3: Auth routes (`/login`, `/auth/callback`, `/logout`)

**Files:**
- Create: `src/web/routers/auth_routes.py`
- Modify: `src/web/main.py` (register the new router)
- Test: `tests/web/test_auth_routes.py`

**Interfaces:**
- Consumes: `oauth.authorize_url`, `oauth.exchange_code`, `oauth.logout_url` (Task 2); `verify_jwt`, `_get_jwks`, `AuthError` from `src.web.auth`; `Settings.cookie_secure` (Task 1).
- Produces: an `APIRouter` named `router`; module-level constants `STATE_COOKIE = "oauth_state"` and `TOKEN_COOKIE = "id_token"`. `TOKEN_COOKIE` MUST equal the cookie name read by `get_user` in [src/web/routers/pages.py:14](../../../src/web/routers/pages.py#L14) (`"id_token"`).

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_auth_routes.py`:

```python
from fastapi.testclient import TestClient

from src.web import oauth
from src.web.main import create_app
from src.web.routers import auth_routes
from src.web.settings import Settings


def _settings():
    return Settings(
        auth_disabled=False,
        cognito_domain="ijor-marketplace",
        s3_region="ap-south-1",
        cognito_pool_id="pool",
        cognito_client_id="cid",
        cognito_client_secret="sec",
        cognito_redirect_uri="http://localhost:8000/auth/callback",
    )


def _client():
    return TestClient(create_app(_settings()), follow_redirects=False)


def test_login_redirects_and_sets_state_cookie():
    r = _client().get("/login")
    assert r.status_code == 302
    assert "oauth2/authorize" in r.headers["location"]
    assert r.cookies.get("oauth_state")


def test_callback_rejects_state_mismatch():
    c = _client()
    c.cookies.set("oauth_state", "expected")
    r = c.get("/auth/callback?code=x&state=WRONG")
    assert r.status_code == 400


def test_callback_happy_path_sets_token_cookie(monkeypatch):
    monkeypatch.setattr(oauth, "exchange_code",
                        lambda settings, code: {"id_token": "TOK"})
    monkeypatch.setattr(auth_routes, "verify_jwt",
                        lambda token, settings, jwks: {"email": "u@x"})
    monkeypatch.setattr(auth_routes, "_get_jwks", lambda settings: {})
    c = _client()
    c.cookies.set("oauth_state", "s1")
    r = c.get("/auth/callback?code=abc&state=s1")
    assert r.status_code == 302
    assert r.headers["location"] == "/"
    assert r.cookies.get("id_token") == "TOK"


def test_callback_redirects_to_login_on_exchange_failure(monkeypatch):
    from src.web.auth import AuthError

    def boom(settings, code):
        raise AuthError("bad code")

    monkeypatch.setattr(oauth, "exchange_code", boom)
    c = _client()
    c.cookies.set("oauth_state", "s1")
    r = c.get("/auth/callback?code=abc&state=s1")
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_logout_clears_cookie_and_redirects_to_cognito():
    c = _client()
    r = c.get("/logout")
    assert r.status_code == 302
    assert "/logout?" in r.headers["location"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/web/test_auth_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.web.routers.auth_routes'`.

- [ ] **Step 3: Implement `src/web/routers/auth_routes.py`**

```python
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from src.web import oauth
from src.web.auth import AuthError, _get_jwks, verify_jwt

router = APIRouter()

STATE_COOKIE = "oauth_state"
TOKEN_COOKIE = "id_token"


def _settings(request):
    return request.app.state.settings


@router.get("/login")
def login(request: Request):
    settings = _settings(request)
    state = secrets.token_urlsafe(24)
    resp = RedirectResponse(oauth.authorize_url(settings, state), status_code=302)
    resp.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True,
                    samesite="lax", secure=settings.cookie_secure, path="/")
    return resp


@router.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = ""):
    settings = _settings(request)
    if not state or state != request.cookies.get(STATE_COOKIE):
        return JSONResponse({"detail": "invalid state"}, status_code=400)
    try:
        tokens = oauth.exchange_code(settings, code)
        verify_jwt(tokens["id_token"], settings, _get_jwks(settings))
    except AuthError:
        return RedirectResponse("/login", status_code=302)
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(TOKEN_COOKIE, tokens["id_token"], httponly=True,
                    samesite="lax", secure=settings.cookie_secure, path="/")
    resp.delete_cookie(STATE_COOKIE, path="/")
    return resp


@router.get("/logout")
def logout(request: Request):
    settings = _settings(request)
    resp = RedirectResponse(oauth.logout_url(settings), status_code=302)
    resp.delete_cookie(TOKEN_COOKIE, path="/")
    return resp
```

- [ ] **Step 4: Register the router in `create_app`**

In `src/web/main.py`, update the router imports/registration block:

```python
    from src.web.routers import pages, generate, fix, auth_routes
    app.include_router(pages.router)
    app.include_router(generate.router)
    app.include_router(fix.router)
    app.include_router(auth_routes.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_auth_routes.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add src/web/routers/auth_routes.py src/web/main.py tests/web/test_auth_routes.py
git commit -m "feat(web): add /login, /auth/callback, /logout routes"
```

---

### Task 4: Browser-aware `AuthError` handler

**Files:**
- Modify: `src/web/main.py` (the `_auth_handler` exception handler)
- Test: `tests/web/test_auth_routes.py` (append)

**Interfaces:**
- Consumes: nothing new — reacts to `AuthError` raised by existing `get_user`/`current_user`.
- Produces: navigation requests get `302 → /login`; HTMX requests (`HX-Request: true`) get a `200` empty response with header `HX-Redirect: /login`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_auth_routes.py`:

```python
def _protected_client():
    # auth on, no token -> get_user raises AuthError on any protected page
    s = Settings(auth_disabled=False, cognito_pool_id="p", cognito_client_id="c",
                 s3_region="ap-south-1", s3_bucket="b")
    return TestClient(create_app(s), follow_redirects=False)


def test_unauthed_navigation_redirects_to_login():
    r = _protected_client().get("/")
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_unauthed_htmx_gets_hx_redirect():
    r = _protected_client().get("/", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert r.headers["HX-Redirect"] == "/login"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/web/test_auth_routes.py -k unauthed -v`
Expected: FAIL — current handler returns `401` JSON, so both assertions fail (status `401`, no `Location`/`HX-Redirect`).

- [ ] **Step 3: Update the handler**

In `src/web/main.py`, replace the imports line and the handler. Update the response import:

```python
from fastapi.responses import RedirectResponse, JSONResponse, Response
```

Replace the handler body:

```python
    @app.exception_handler(AuthError)
    async def _auth_handler(request: Request, exc: AuthError):
        if request.headers.get("HX-Request") == "true":
            resp = Response(status_code=200)
            resp.headers["HX-Redirect"] = "/login"
            return resp
        return RedirectResponse("/login", status_code=302)
```

(`JSONResponse` may now be unused in `main.py`; remove it from the import if so.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_auth_routes.py -v`
Expected: PASS (7 tests total in the file).

- [ ] **Step 5: Full web suite regression**

Run: `python -m pytest tests/web -v`
Expected: PASS — existing `test_auth.py`, `test_pages.py`, `test_generate.py`, `test_fix.py`, `test_jobs.py`, `test_settings.py` all still green.

- [ ] **Step 6: Commit**

```bash
git add src/web/main.py tests/web/test_auth_routes.py
git commit -m "feat(web): redirect unauthenticated requests to /login (HTMX-aware)"
```

---

### Task 5: Deploy runbook + architecture docs

**Files:**
- Modify: `docs/runbooks/web-ec2-deploy-console.md` (append a "Stage 2 — enable real auth" section)
- Modify: `docs/ARCHITECTURE.md` (add `oauth.py` + `auth_routes.py` to the module map)

This task is documentation + a manual console/verification checklist (no pytest). It is the integration deliverable that turns the code from Tasks 1–4 into a working login on EC2.

- [ ] **Step 1: Append the deploy section to the runbook**

Add to `docs/runbooks/web-ec2-deploy-console.md`:

```markdown
## Stage 2 — enable real auth (My-IP-only, no TLS)

Prereq: the app image includes the /login, /auth/callback, /logout routes
(plan 2026-07-01-web-auth-stage2-cognito-login).

1. **Cognito app client** (`marketplace-listing-pool`, pool `ap-south-1_NdxNQ1plz`):
   - Token expiration → set **ID token** validity to **8 hours** (keeps re-logins rare).
   - Allowed callback URLs → add `http://<EC2_PUBLIC_IP>/auth/callback`
     (keep `http://localhost:8000/auth/callback` for local dev).
   - Allowed sign-out URLs → add `http://<EC2_PUBLIC_IP>/`.
2. **SSM Parameter Store** (`/marketplace-listing/`): confirm `cognito_domain`,
   `cognito_client_id`, `cognito_pool_id`, `s3_region` are set, and set
   `cognito_redirect_uri = http://<EC2_PUBLIC_IP>/auth/callback`. The client
   secret stays in Secrets Manager. Confirm the `listing-app-ec2-role` policy
   allows `ssm:GetParameter` + `secretsmanager:GetSecretValue` on those paths.
3. **systemd unit** `listing-app.service`: remove `AUTH_DISABLED=1`. Leave
   `COOKIE_SECURE` unset (off) — there is no TLS yet.
4. **Redeploy:** `sudo systemctl restart listing-app` (re-pulls `:latest`).
5. **Verify** from the allowed IP: hit `/` → bounced to Cognito hosted UI →
   log in → land back on the dashboard; `/logout` clears the session.
6. **Keep access My-IP-only.** Do NOT open the SG to `0.0.0.0/0` — with no TLS
   the id_token cookie is sniffable in transit (a later chunk adds TLS + public).

**IP-change caveat:** stopping/starting the instance changes its public IP,
which breaks the registered callback. On restart, update the Cognito callback +
sign-out URLs and the `cognito_redirect_uri` SSM param to the new IP. (Add an
Elastic IP later only if restarts become frequent — it is free while attached to
a running instance, but billed while the instance is stopped.)
```

- [ ] **Step 2: Update the architecture module map (and fix now-stale notes)**

In `docs/ARCHITECTURE.md`, add two rows to the web-app module table (after the `src/web/routers/fix.py` row):

```markdown
| `src/web/oauth.py` | Hosted-UI OAuth helpers (`authorize_url`/`exchange_code`/`logout_url`); stdlib urllib, injectable `http` so unit tests never hit the network. |
| `src/web/routers/auth_routes.py` | `GET /login` (state CSRF cookie → hosted UI), `GET /auth/callback` (verify state, exchange code, set `id_token` cookie), `GET /logout`. Sessions are **re-login-on-stale** (no refresh tokens). |
```

Then update two stale lines:
- The `src/web/main.py` row: change `includes routers (pages, generate, fix)` → `includes routers (pages, generate, fix, auth_routes)`, and `maps AuthError → 401` → `maps AuthError → redirect to /login (HX-Redirect for HTMX)`.
- The Cognito integration row (`**Cognito (auth)**`): change `Hosted-UI login round-trip (/auth/callback) not built yet.` → `Hosted-UI login round-trip (/login → /auth/callback → /logout) built; enable by dropping AUTH_DISABLED.`

- [ ] **Step 3: Commit**

```bash
git add docs/runbooks/web-ec2-deploy-console.md docs/ARCHITECTURE.md
git commit -m "docs: Stage 2 auth deploy runbook + architecture module map"
```

---

## Self-Review

**Spec coverage:**
- oauth.py (authorize/exchange/logout) → Task 2 ✓
- /login, /auth/callback (state CSRF guard + cookie), /logout → Task 3 ✓
- Browser-aware AuthError handler (302 nav / HX-Redirect htmx) → Task 4 ✓
- `cookie_secure` setting, Secure off until TLS → Task 1 ✓
- Cognito id-token validity ~8h, prod callback/sign-out registration → Task 5 (console) ✓
- SSM/Secrets config, drop AUTH_DISABLED, redeploy, IP-change caveat, keep My-IP-only → Task 5 ✓
- Re-login-on-stale / no refresh / no middleware → reflected by leaving `get_user`/`verify_jwt` untouched (no task changes them) ✓
- Tests: oauth funcs, /login, callback state-mismatch + happy path, logout, unauth redirect, cookie_secure → Tasks 1–4 ✓

**Placeholder scan:** `<EC2_PUBLIC_IP>` in Task 5 is an intentional per-deploy value in a console runbook, not a code placeholder. No TBD/TODO in code steps; every code step shows full code.

**Type consistency:** `TOKEN_COOKIE = "id_token"` (Task 3) matches the cookie `get_user` reads in `pages.py`. `exchange_code(settings, code, http=...)` signature is consistent between Task 2 (definition/tests) and Task 3 (called as `oauth.exchange_code(settings, code)`). `verify_jwt(token, settings, jwks)` and `_get_jwks(settings)` match `auth.py` and the Task 3 monkeypatch signatures. `authorize_url(settings, state)` / `logout_url(settings)` consistent across Tasks 2–3.
