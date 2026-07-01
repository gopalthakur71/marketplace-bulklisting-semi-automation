# Stage 2 — Real Auth (Cognito hosted-UI login) — Design

**Date:** 2026-07-01
**Status:** Approved (design), pending implementation plan
**Scope:** App-code auth (login/callback/logout) + deploy to EC2 keeping "My-IP-only" access. TLS, a stable public URL, and opening to `0.0.0.0/0` are explicitly **out of scope** (a later chunk).

## Problem

The Marigold Ops FastAPI app runs with `AUTH_DISABLED=1` (dev user `dev@local`) both locally and on EC2. Every route already gates on `get_user(request)`, and [src/web/auth.py](../../../src/web/auth.py) already verifies Cognito JWTs (`verify_jwt`, RS256, audience + issuer checks) and reads an `id_token` cookie. What's missing is the **login round-trip**: there are no `/login`, `/auth/callback`, or `/logout` routes, so a real login 404s at the callback. Cognito infra is fully provisioned (pool `ap-south-1_NdxNQ1plz`, confidential app client `29oo5dtqh8j50k2481lmffqb0e`, domain `ijor-marketplace`, authorization-code grant).

This design fills that gap so we can drop `AUTH_DISABLED` and require a logged-in user.

## Decisions (from brainstorming)

- **Session model: re-login on stale.** No refresh tokens, no refresh cookie, no middleware. When the `id_token` expires the user is bounced to `/login`. To keep re-logins infrequent, bump the Cognito app-client **id-token validity to ~8h** (console config, no code).
- **No Elastic IP.** The running instance already pays for its auto-assigned public IPv4; an EIP would only add cost while the instance is *stopped*. Instead: keep the instance running (its public IP is stable until a deliberate stop/start), and re-register the callback URL in Cognito on the rare restart (documented in the runbook).
- **Cookies are not `Secure` yet.** No TLS in scope, so the `id_token` cookie is set `HttpOnly; SameSite=Lax` but **not** `Secure` (a `Secure` cookie won't be sent over plain HTTP). Acceptable only because access is IP-restricted. A `COOKIE_SECURE` setting (default off) lets TLS flip it on later with no code change.

## Architecture

Because there is no silent refresh, the existing request-time auth path is untouched: routes call `get_user(request)` → `current_user(settings, token)` → `verify_jwt`. We only add the **OAuth login endpoints** plus a **browser-aware `AuthError` handler**.

### New module: `src/web/oauth.py`

Pure functions, stdlib `urllib` only (matches [auth.py](../../../src/web/auth.py); no new dependency). The outbound HTTP call is injectable so tests never hit the network.

- `authorize_url(settings, state) -> str`
  Builds `https://{cognito_domain}.auth.{s3_region}.amazoncognito.com/oauth2/authorize` with `response_type=code`, `client_id`, `redirect_uri=settings.cognito_redirect_uri`, `scope="openid email"`, `state`.
- `exchange_code(settings, code, http=_post) -> dict`
  POST to `/oauth2/token`, `grant_type=authorization_code`, `code`, `redirect_uri`, with the **client secret via HTTP Basic auth** (`Authorization: Basic base64(client_id:client_secret)`). Returns the token JSON (`id_token`, `access_token`, `refresh_token`) — we use only `id_token`.
- `logout_url(settings) -> str`
  Builds `/logout?client_id=...&logout_uri={sign-out URL}`.
- `_post(url, data, headers) -> dict` — the default injectable HTTP helper (urllib).

`exchange_code` raises `AuthError` (reused from `auth.py`) on a non-200 / malformed token response.

### New router: `src/web/routers/auth_routes.py`

- `GET /login`
  Generate a random `state` (`secrets.token_urlsafe`), set it in a short-lived `oauth_state` cookie (`HttpOnly; SameSite=Lax`, ~10 min), redirect (`302`) to `authorize_url(settings, state)`.
- `GET /auth/callback?code=&state=`
  1. Reject (`400`) if `state` query param is missing or does not equal the `oauth_state` cookie (CSRF guard).
  2. `exchange_code(settings, code)`; on `AuthError` → `302` redirect to `/login`.
  3. Verify the returned `id_token` with the existing `verify_jwt` (defence in depth).
  4. Set the `id_token` cookie (`HttpOnly; SameSite=Lax; Path=/`; `Secure` iff `settings.cookie_secure`), clear `oauth_state`, redirect (`302`) to `/`.
- `GET /logout`
  Clear the `id_token` cookie, redirect to `logout_url(settings)` (Cognito clears its own session, then redirects to the registered sign-out URL).

Router is registered in `create_app` alongside the existing `pages`, `generate`, `fix` routers. Its three paths are the only routes that must remain reachable without a valid session.

### Browser-aware `AuthError` handler

Replace the current JSON-only handler in [src/web/main.py](../../../src/web/main.py):

- If the request is an HTMX request (`HX-Request: true` header) → respond `200` with an `HX-Redirect: /login` header (empty body) so HTMX performs a client-side redirect instead of swapping a 401 body into the page.
- Else (full-page navigation) → `302` redirect to `/login`.

The public auth routes never call `get_user`, so they are unaffected.

### Settings additions ([src/web/settings.py](../../../src/web/settings.py))

- `cookie_secure: bool = False` — resolved from env `COOKIE_SECURE` (`"1"/"true"`), like `auth_disabled`. Not an SSM field.
- `cognito_redirect_uri` and the other `cognito_*` fields already exist in `_FIELDS`; no schema change. Local value: `http://localhost:8000/auth/callback`. Prod value: `http://<ec2-public-ip>/auth/callback` (set in SSM at deploy).

## Data flow (happy path)

1. Unauthenticated user hits `/` → `get_user` raises `AuthError` → handler `302 → /login`.
2. `/login` → set `oauth_state` cookie → `302` to Cognito hosted UI.
3. User authenticates at Cognito → Cognito `302`s back to `/auth/callback?code=…&state=…`.
4. `/auth/callback` verifies `state`, exchanges `code` for tokens, verifies `id_token`, sets the `id_token` cookie, clears `oauth_state`, `302 → /`.
5. `/` → `get_user` reads `id_token` cookie → `verify_jwt` OK → page renders.
6. After ~8h the token expires → next request raises `AuthError` → back to step 1.

## Error handling

- **`state` missing/mismatch** at callback → `400` (CSRF rejected); do not exchange the code.
- **Token exchange failure** (bad code, Cognito error, non-200) → `AuthError` → redirect to `/login`.
- **Malformed / unverifiable `id_token`** → `AuthError` → redirect to `/login`.
- **Expired token on a normal request** → `AuthError` → redirect to `/login`.
- **JWKS fetch** (existing `_get_jwks`) requires outbound internet from EC2 — already available.

## Deploy (EC2, still My-IP-only)

Done via the console runbook, appended to [docs/runbooks/web-ec2-deploy-console.md](../../../docs/runbooks/web-ec2-deploy-console.md):

1. **Cognito app client:** set id-token validity ~8h; add allowed callback URL `http://<ec2-public-ip>/auth/callback` and sign-out URL `http://<ec2-public-ip>/` (keep the existing localhost entries for local dev).
2. **SSM Parameter Store:** confirm/set the `cognito_*` params under `/marketplace-listing/`, including `cognito_redirect_uri = http://<ec2-public-ip>/auth/callback`. Client secret stays in Secrets Manager. Confirm the `listing-app-ec2-role` policy allows `ssm:GetParameter` + `secretsmanager:GetSecretValue` on those paths.
3. **systemd unit `listing-app.service`:** remove `AUTH_DISABLED=1` (leave `COOKIE_SECURE` unset → off).
4. **Redeploy** (`:latest` pull on restart) and verify a full login round-trip from the allowed IP.
5. **Runbook note:** on any instance stop/start the public IP changes → update the Cognito callback/sign-out URLs and the `cognito_redirect_uri` SSM param (or add an EIP later if this becomes frequent).
6. Access stays **My-IP-only**; do **not** open `0.0.0.0/0` (no TLS → cookie is sniffable in transit; a later chunk adds TLS + public access).

## Testing

Unit + `TestClient` tests (pytest, matching `tests/web/`):

- `oauth.py`: `authorize_url` shape; `exchange_code` builds the Basic-auth header + form body and parses tokens (fake `http`); `exchange_code` raises `AuthError` on non-200; `logout_url` shape.
- `/login`: `302` to Cognito, `oauth_state` cookie set.
- `/auth/callback`: `state` mismatch → `400`, no exchange; happy path sets `id_token` cookie + `302 → /` (fake `exchange_code` + stubbed `verify_jwt`).
- `/logout`: clears `id_token` cookie, `302` to Cognito logout.
- `AuthError` handler: HTMX request → `HX-Redirect`; plain navigation → `302 /login`.
- `cookie_secure` toggles the `Secure` attribute.
- Existing `tests/web/test_auth.py` (`current_user` dev bypass + missing-token) stays green.

## Out of scope (later chunks)

- TLS certificate + stable public URL, then opening to `0.0.0.0/0`.
- Refresh-token / silent-refresh sessions.
- Elastic IP (only if restarts become frequent).
- Multi-user / role-based authorization (single founder user for now).
- The accepted IDOR on fix/generate sessions (unchanged here).
