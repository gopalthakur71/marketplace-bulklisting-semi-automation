# Design — Listing Web App, Phase 2 (FastAPI web layer)

Date: 2026-06-29
Status: approved (brainstorm) → next: implementation plan
Supersedes nothing; **narrows and updates** the web-app portion of
`docs/superpowers/specs/2026-06-25-listing-web-app-cloud-deploy-design.md` with the
decisions made on 2026-06-29.

## 1. Purpose & scope

Wrap the already-built, already-merged deterministic Shopify → Myntra pipeline in a
web UI so non-technical teammates can (A) generate Myntra upload sheets and (B) fix
rows Myntra rejected — without touching code, Python, or AWS credentials.

The backend logic this app calls is **done and tested** (41 passing tests, on `main`):
`src/myntra/pipeline.main()`, `error_reader.read_errors()`,
`corrector.plan_corrections()/correct()`, `groupid_ledger.reserve()/confirm()`, plus
`src/core` (shopify_reader, images, s3_upload). **This phase does not change that
logic** — it only adds a presentation layer that calls it.

**Scope of THIS build (decided 2026-06-29 — "full design at once" minus deploy):**
the FastAPI app + Cognito auth + SSM/Secrets config + Dockerfile. The EC2/systemd
deploy is **out of scope** (Phase 4, later). The locked visual direction is "Marigold
Ops" (see `web-app-ui-direction` memory and `mockups/05`, `mockups/07`).

## 2. Key decisions (2026-06-29)

1. **Full-scope build, no deploy.** App + Cognito + SSM/Secrets + Dockerfile now; EC2
   deploy deferred to Phase 4.
2. **Layered config + auth toggle.** One settings loader reads **env vars first**, then
   falls back to **SSM Parameter Store / Secrets Manager**. An `AUTH_DISABLED=1` env flag
   bypasses Cognito locally (injects a fake dev user). Identical code in both modes; only
   the env differs. This lets the whole app be built and tested locally with **no AWS
   reachable**, then have Cognito and SSM/Secrets switched on one at a time.
3. **No EC2 needed to develop.** SSM, Secrets Manager, and Cognito are standalone AWS
   services usable from the laptop with existing local IAM keys. The EC2 instance role is
   only a deploy-time convenience (Phase 4) and requires **zero code change** thanks to
   the layered loader.
4. **Styling = reuse the mockup CSS.** Lift the hand-written CSS from the approved
   mockups (`05-marigold-ops.html`, `07-marigold-home.html`) into one static stylesheet.
   **No Tailwind, no Node, no build step, no runtime CDN.** (This overrides the
   "Tailwind" wording in earlier notes/memory.)
5. **Progress = background job + htmx polling.** Generate kicks off an in-process
   background task; an in-memory job store tracks the current step + counts; htmx polls a
   status endpoint (~1 s) and lights up the pipeline stepper live.
6. **Fix-errors corrections are typed, not picked.** The user types the corrected value
   into a free-text input; it is validated against Myntra's real vocabulary before
   writing (per `vocab-must-match-template`). A non-clickable "closest Myntra names" hint
   is allowed; **no option buttons.**

## 3. Architecture & repository placement

New presentation layer `src/web/`, marketplace-agnostic, calling the existing backend.
Nothing in `src/core` or `src/myntra` logic changes.

```
src/web/
  __init__.py
  main.py            # FastAPI app factory, route registration, startup config load
  settings.py        # layered config loader (env -> SSM/Secrets) + feature flags
  auth.py            # Cognito JWT validation + AUTH_DISABLED dev bypass
  jobs.py            # in-memory job store: create/update/get, status enum, steps
  routers/
    pages.py         # landing/home + shared nav (GET pages)
    generate.py      # Flow A: upload CSV -> background job -> result + ledger confirm
    fix.py           # Flow B: upload error file -> classify -> typed fixes -> regenerate
  templates/         # Jinja: base.html, home.html, generate.html, fix.html,
                     #        _stepper.html, _result.html, _fix_review.html
  static/
    app.css          # lifted from mockups 05 + 07 (Marigold Ops)
    htmx.min.js      # vendored (no runtime CDN)
    fonts/           # or vendored webfont @font-face (no runtime CDN)
  runtime/           # per-job temp dirs (gitignored), cleaned after download
```

**Entry points:** `uvicorn src.web.main:app --reload` locally; the Dockerfile `CMD`
becomes `uvicorn src.web.main:app --host 0.0.0.0 --port 8080`. `run.py` (CLI) is
unchanged.

**Dependencies added:** `fastapi`, `uvicorn[standard]`, `jinja2`,
`python-multipart` (uploads), `python-jose[cryptography]` (JWT verify). `boto3`,
`openpyxl`, `pyyaml` already present. No Node, no Tailwind, no Celery/Redis/DB.

## 4. Settings & auth (the layered loader)

`settings.py` exposes one `Settings` object resolved at startup:

| Value | Local source | Cloud source (Phase 4) |
|---|---|---|
| S3 bucket, region, prefix | env / `.env` | SSM Parameter Store |
| Cognito user-pool id, client id, domain | env / `.env` (or unset) | SSM |
| Cognito client secret | env / `.env` | Secrets Manager |
| AWS credentials (S3, SSM, Secrets) | local IAM keys (default chain) | EC2 instance role |
| Auth enforcement | `AUTH_DISABLED=1` → fake dev user | real Cognito JWT |

Resolution rule per value: **if the env var is set, use it; else fetch from
SSM/Secrets** (boto3, default credential chain). SSM/Secrets are only contacted for
values not supplied via env, so local dev with a full `.env` makes zero AWS calls for
config.

`auth.py`: a FastAPI dependency on protected routes. When `AUTH_DISABLED=1`, it returns
a fixed dev user and skips all Cognito work. Otherwise it runs the Cognito hosted-UI
redirect login and validates the returned JWT (signature against the pool's JWKS,
issuer, audience, expiry) on every request. Adding/removing teammates is done in the
Cognito console — no code change.

## 5. Flow A — Generate (3 screens)

1. **Upload** (`GET /` → `POST /generate`): drop `products_export.csv`; marketplace =
   Myntra (only one enabled); the styleGroupId start is read from the **ledger** and
   shown read-only (not typed). `POST /generate` validates the upload is a CSV, calls
   `groupid_ledger.reserve(count, filename)` to get `(start, batch_id)`, creates a job,
   starts a background task, and returns a `job_id` immediately.
2. **Progress**: the background task runs `pipeline.main(csv_path=..., out_dir=<job
   tmp>, style_group_id_start=start)`, updating the job store's current step
   (Ingest → Map → Images→S3 → Fill & validate → Ready) and counts. htmx polls
   `GET /jobs/{job_id}` (~1 s) and re-renders the `_stepper.html` fragment.
3. **Result**: on completion `GET /jobs/{job_id}` renders `_result.html` — download link
   for `myntra_filled.xlsx`, inline report (rows written, images uploaded, vocab flags,
   blanks), the **assigned styleGroupId range**, and a **"Mark upload successful"**
   button → `POST /generate/{batch_id}/confirm` calls `groupid_ledger.confirm(batch_id)`,
   which advances the counter. Reserve does **not** advance; only confirm does, so a
   failed/abandoned upload frees its IDs for reuse.

Job temp dirs under `src/web/runtime/<job_id>/` hold the uploaded CSV and outputs; the
result file is streamed on download; the dir is cleaned after download / on job expiry.

## 6. Flow B — Fix errors (3 screens)

1. **Upload** (`GET /fix` → `POST /fix`): drop the Myntra rejection `.xlsx`. The flow is
   self-contained — the error file already holds every submitted value (including S3
   image URLs), so no Shopify CSV is needed.
2. **Review & type fixes** (`POST /fix` renders `_fix_review.html`): call
   `error_reader.read_errors(path, rules)` then `corrector.plan_corrections(row_errors)`
   to sort each row into three buckets driven by `config/myntra/error_rules.yaml`:
   - **auto-fixed** (e.g. pincode from constants, empty ISP → price): shown as done, no
     input.
   - **needs you**: a **free-text input** to type a vocab value (e.g. colour), and/or a
     "Drop this SKU" checkbox for already-listed duplicates. A read-only "closest Myntra
     names" hint may be shown (not buttons).
   - **explain-only** (image/account/brand issues): plain-English guidance + the raw
     message; no input (e.g. image errors are fixed upstream by re-running Generate).
3. **Apply & regenerate** (`POST /fix/apply`): collect typed `answers` + `drops`, call
   `corrector.correct(row_errors, template, template_path, constants, answers, drops,
   out_path)`. Every typed value is validated via the mapper against the template
   vocabulary; values that still don't match are reported as `rejected` (never silently
   written). Render a result with the change summary and a download link for the
   corrected sheet.

## 7. Job execution model (no extra infra)

In-process `BackgroundTasks` (or a thread) per job; an **in-memory dict** job store
(`jobs.py`) keyed by `job_id` holding status (`queued|running|done|error`), current step,
counts, result paths, and error text. htmx polling reads it. No Celery, no Redis, no DB.
State is intentionally ephemeral — the only durable state is the S3 styleGroupId ledger
(already built). A single worker process is assumed (internal, low-traffic tool).

## 8. Styling

`static/app.css` is the hand-written Marigold Ops CSS lifted from the approved mockups:
warm near-black bg `#191613` / panel `#221E1A`, marigold accent `#E8A33D`, success green
`#7BB87A`; Space Grotesk (display) + IBM Plex Mono (data) + Inter (body). Fonts and
`htmx.min.js` are vendored into `static/` so the running app needs no external CDN.
Templates are server-rendered Jinja; htmx swaps fragments for the stepper and fix-review.

## 9. Dockerfile & build order

**Dockerfile**: extend the existing `python:3.12-slim` image (deps cached before src);
the only substantive change is `CMD` → `uvicorn src.web.main:app --host 0.0.0.0
--port 8080`. Static assets vendored in the image. No Node, no multi-stage build.

**Build order** (each step independently runnable & testable):
1. Local app, no AWS — both flows, `AUTH_DISABLED=1`, config from `.env`, S3 via local
   keys. Full app works on localhost; all tests pass. *(bulk of the work)*
2. Cognito — console runbook (`docs/runbooks/...`) → flip auth on, log in from
   localhost.
3. SSM + Secrets — console runbook → move config out of `.env` into AWS, read with local
   keys.
4. Dockerfile — update `CMD`, build & smoke-test locally.

Console runbooks for steps 2 & 3 are written in the same pre-filled, click-through style
as the existing `docs/runbooks/cicd-aws-setup-console.md`, and walked through when the
code is ready to consume them.

## 10. Testing

Reuse the existing 41-test suite untouched. Add web-layer tests with the
pipeline/corrector **stubbed** (fast, offline):
- **Settings loader** — env-first resolution; SSM/Secrets fallback with a stubbed boto3.
- **Auth** — `AUTH_DISABLED` injects dev user; missing/invalid JWT rejected when auth is
  on.
- **Generate router** — non-CSV upload rejected; job lifecycle (create → poll → done);
  `reserve` called on submit; confirm endpoint advances the ledger counter.
- **Fix router** — error file → buckets rendered; typed value validated (good accepted,
  invalid → `rejected`); drop-SKU honoured.
- **Jobs store** — step transitions and status reporting.

CI (already wired, `.github/workflows/ci-cd.yml`) runs the whole suite as the gate.

## 11. Out of scope (YAGNI)

EC2/systemd deploy (Phase 4); database (S3 JSON ledger suffices until SaaS/multi-tenant);
React/SPA; Tailwind/Node; Celery/Redis; per-user history/audit beyond the ledger; Myntra
API integration (uploads stay manual via the portal); marketplaces other than Myntra
(structure already supports adding them); ledger concurrency guard (single-batch workflow
assumed, see 2026-06-25 spec §6).

## 12. Components summary

| Unit | Does | Depends on |
|---|---|---|
| `src/web/main.py` | App factory, routes, startup config | settings, auth, routers |
| `src/web/settings.py` | Layered config (env → SSM/Secrets) | boto3 |
| `src/web/auth.py` | Cognito JWT validate / dev bypass | python-jose, settings |
| `src/web/jobs.py` | In-memory job store + step tracking | — |
| `src/web/routers/generate.py` | Flow A: upload → job → result → confirm | pipeline, groupid_ledger, jobs |
| `src/web/routers/fix.py` | Flow B: upload → classify → typed fixes → regenerate | error_reader, corrector, mapper |
| `src/web/routers/pages.py` | Landing + nav | templates |
| `src/web/templates/`, `static/` | Marigold Ops UI (Jinja + CSS + htmx) | — |
| `Dockerfile` | One-container image (CMD → uvicorn) | — |
| `docs/runbooks/*` | Console click-through for Cognito + SSM/Secrets | — |
