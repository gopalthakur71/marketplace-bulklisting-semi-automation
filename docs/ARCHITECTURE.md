# Architecture & File Map

Complete map of the **Myntra Bulk-Listing Automation** codebase: what every part does, how
data flows, and where each external system plugs in. Written so any coding agent (or human)
can locate "what comes from where" without prior context. For orientation + invariants see
[../AGENTS.md](../AGENTS.md); for usage + Myntra upload rules see [README.md](../README.md);
for *why* decisions were made see [decisions/](decisions/).

---

## 0. Troubleshooting index — "when X breaks, look here"

| Symptom | Start at |
|---|---|
| A Myntra attribute is wrong/blank in the output sheet | `src/myntra/mapper.py` + `config/myntra/{rules,constants,column_map}.yaml` |
| Myntra rejects a value as not in the dropdown | `src/myntra/mapper.py` (`validate_value`) + `src/myntra/template_reader.py` (vocab parse). **Invariant: value must match template spelling.** |
| Image rejected (`.webp` / extension / size) | `src/core/images.py` + `src/core/s3_upload.py` + `config/myntra/image_specs.yaml` |
| styleGroupId wrong, duplicated, or skipped | `src/myntra/groupid_ledger.py` (reserve vs confirm) + `style_group_id_start` in `rules.yaml` |
| Sheet structurally rejected (`SHEET_VALIDATION_FAILED`, null brand) | `src/myntra/fill.py` (clears stray rows; inline strings; dropdowns) — see journal 2026-06-24/25 |
| Rejection file not classified / wrong fix bucket | `src/myntra/error_reader.py` + `config/myntra/error_rules.yaml` |
| Web app returns 401 / login issues | `src/web/auth.py` + Cognito settings; locally set `AUTH_DISABLED=1` |
| Generate job stuck / disappears | `src/web/jobs.py` (**in-memory, lost on restart**) + `src/web/routers/generate.py` |
| Config not loading on the server | `src/web/settings.py` (per-field env→SSM/Secrets) + EC2 instance role perms |
| CI build/push fails | `.github/workflows/ci-cd.yml` + `aws/cicd/*` (OIDC role/trust) |
| Deploy / EC2 issues | `docs/runbooks/web-ec2-deploy-console.md` |

---

## 1. System map (the four layers)

```
                         ┌─────────────────────────── Layer 3: Web app (src/web, FastAPI) ──┐
                         │  Flow A Generate          Flow B Fix                              │
  browser ──────────────►  upload CSV → job → xlsx   upload rejection.xlsx → corrected xlsx │
   (Cognito/AUTH_DISABLED)│        │                          │                              │
                         └────────┼──────────────────────────┼──────────────────────────────┘
                                  │ calls                     │ calls
        ┌─────────────────────────▼───────────┐   ┌──────────▼──────────────────────────────┐
        │ Layer 1: Core fill pipeline          │   │ Layer 2: Error-correction backend        │
        │ src/core + src/myntra + config/myntra│   │ src/myntra/{groupid_ledger,error_reader, │
        │ run.py  →  myntra_filled.xlsx + S3   │   │            corrector}                    │
        └──────────────┬───────────────────────┘   └──────────────────────────────────────────┘
                       │ images → S3, ledger → S3
        ┌──────────────▼───────────────────────── Layer 4: Cloud / CI-CD / deploy ───────────┐
        │ Dockerfile → GitHub Actions (OIDC) → ECR → EC2 (instance role) ; Cognito ; SSM/Secrets│
        └───────────────────────────────────────────────────────────────────────────────────┘
```

Layer 3 contains **no business logic** — it orchestrates layers 1–2. Layers 1–2 have **no web
dependency** and are fully unit-tested on their own.

---

## 2. Repo layout (annotated)

```
run.py                         # CLI entry → src.myntra.pipeline.cli()
AGENTS.md                      # agent/human orientation (read first)
README.md                      # usage + Myntra upload rules
requirements.txt               # Python deps (3.12)
Dockerfile / .dockerignore     # image: uvicorn src.web.main:app on :8080

src/
  core/                        # marketplace-agnostic (shared by every marketplace)
    models.py shopify_reader.py images.py s3_upload.py
  myntra/                      # Myntra-specific
    pipeline.py template_reader.py mapper.py fill.py report.py     # Layer 1
    groupid_ledger.py error_reader.py corrector.py                 # Layer 2
  web/                         # Layer 3 (FastAPI app)
    main.py settings.py auth.py jobs.py
    routers/ pages.py generate.py fix.py
    templates/ *.html          # Jinja (base + home + generate + fix + htmx partials)
    static/ app.css htmx.min.js fonts/*.woff2   # vendored, no CDN
    runtime/                   # per-job working dirs (git-ignored except .gitkeep)

config/myntra/                 # behaviour-as-config (edit instead of code)
  column_map.yaml constants.yaml rules.yaml image_specs.yaml error_rules.yaml
templates/myntra/              # blank Myntra template + a known-good reference upload
input/                         # Shopify export (git-ignored)
output/                        # generated sheets/images/report (git-ignored)
errors/myntra/                 # captured rejection files (git-ignored)

aws/cicd/                      # OIDC trust + ECR push/lifecycle policies (CI identity)
S3/                            # IAM + bucket policies for image hosting (app→S3 identity)
.github/workflows/ci-cd.yml    # test gate → build → push image to ECR

docs/
  ARCHITECTURE.md (this file)  decisions/ (ADRs / why)  runbooks/ (ops)
  superpowers/specs/ + plans/  journal/ (history)
tests/                         # 57 tests; tests/web/ covers Layer 3
```

---

## 3. Layer 1 — Core fill pipeline

### Data flow (`python run.py`)

```
 Shopify CSV ─┐
              ├─► shopify_reader ─► [Product]                 (group variants + image gallery)
 Myntra xlsx ─┴─► template_reader ─► TemplateInfo            (headers + 37 dropdown vocabularies)
                                          │
                    mapper.map_product(Product, TemplateInfo, config) ─► MappedRow
                       • constants  • pricing  • rules(HSN/colour)  • vocab validation (flag, never guess)
                                          │
 each Product ─► images.process_images ─► ImageResult         (download → JPG → public S3 .jpg URL)
                       s3_upload.upload_images(...) ─► s3://…/myntra/<sku>/<n>.jpg
                                          │
                    fill.fill_template(rows) ─► output/myntra_filled.xlsx
                    report.write_report(rows) ─► output/report.txt
```

`pipeline.main(csv_path=…, out_dir=…, style_group_id_start=…, upload=…)` orchestrates it;
`run.py` is a thin CLI wrapper. `main(upload=False)` skips S3 (used by tests and offline demos).

### Modules

| File | Responsibility | Key details |
|---|---|---|
| `src/core/models.py` | Shared dataclasses | `Product`, `Flag`, `MappedRow`, `ImageResult`, `TemplateInfo`. |
| `src/core/shopify_reader.py` | Read + group Shopify export | Groups variant/image rows by `Handle`, forward-fills product fields, orders gallery by `Image Position`. |
| `src/core/images.py` | Image conversion | Download → flatten transparency onto white → JPG to `<out>/<sku>/<n>.jpg`; validates size/dims; emits the **public S3 `.jpg` URL** (`public_base_url`). |
| `src/core/s3_upload.py` | Host images | Uploads JPGs to `s3://<bucket>/<prefix>/<sku>/<n>.jpg` as `image/jpeg`; boto3 client injectable for tests. |
| `src/myntra/pipeline.py` | Orchestrator (`main`/`cli`) | Loads `config/myntra/`; assigns `styleGroupId` (offset by `style_group_id_start`); gates S3 use; writes outputs. |
| `src/myntra/template_reader.py` | Read Myntra template | Detects header/data rows; parses the **37 x14 extension dropdowns** from raw sheet XML → `{column → allowed values}`. |
| `src/myntra/mapper.py` | Map + validate + rules | Constants, pricing, HSN-by-fabric, Prominent Colour scan + synonyms, **`validate_value`** (canonicalize to template spelling or flag). Returns `MappedRow`. |
| `src/myntra/fill.py` | Write the Sarees sheet | Numeric cells (`NUMERIC_HEADERS`), S3 image URLs, **clears stray template rows**, shared→inline strings, dropdowns not re-injected by default (`preserve_dropdowns=False`). |
| `src/myntra/report.py` | Audit report | `output/report.txt`: per-SKU filled count, blanks, vocab flags, image pass/fail. |

---

## 4. Layer 2 — Error-correction backend

Drives the web Fix flow; also usable standalone. No web dependency.

| File | Responsibility | Key details |
|---|---|---|
| `src/myntra/groupid_ledger.py` | styleGroupId counter | `read_ledger`/`reserve`/`confirm` over a pluggable store. **`reserve()` records a pending batch but does NOT advance the counter; only `confirm()` advances** (so an unuploaded batch frees its ids). Store = `LocalJsonStore` (dev file) or `S3JsonStore` (key `state/myntra_groupid.json`). |
| `src/myntra/error_reader.py` | Read + classify rejections | Reads the Myntra rejection `.xlsx` (headers row 3, data row 4; error cols `STATUS`, `SYSTEM ERROR MESSAGE`); splits the message on `;` and **classifies each issue via `config/myntra/error_rules.yaml`** into a `{category, action, explanation, field}`. Returns `RowError` per row. |
| `src/myntra/corrector.py` | Apply fixes + regenerate | `plan_corrections` (preview buckets: auto/drop/manual/unknown) and `correct(...)`: drops chosen SKUs, applies deterministic **auto-fixes** (pincode from constants; backfill empty ISP from MRP), applies **user answers vocab-validated** (`validate_value`; invalid → `summary["rejected"]`, never written; mirrors Prominent Colour into Brand Colour Remarks), then regenerates via `fill.fill_template`. |

**Honest-config note:** `error_rules.yaml` marks `image` and `stylegroupid` issues as
`explain_only` (their real fix is upstream S3 hosting / the ledger, not a cell edit) — the
corrector only auto-fixes what it deterministically can.

---

## 5. Layer 3 — Web app (FastAPI, "Marigold Ops")

Wraps layers 1–2 so non-technical staff can run them. FastAPI + Jinja + plain CSS + vendored
htmx. **No business logic here** — routers call `src/myntra` / `src/core`.

### Modules

| File | Responsibility |
|---|---|
| `src/web/main.py` | `create_app()`: settings on `app.state` **before** routers; mounts `/static`; includes routers (`pages`, `generate`, `fix`, `auth_routes`); maps `AuthError → redirect to /login (HX-Redirect for HTMX)`. Module-level `app` + shared `Jinja2Templates`. |
| `src/web/settings.py` | `Settings` dataclass + `load_settings(env, ssm, secrets)`: each field resolves **env-first, then per-field fallback** to SSM (non-secret) / Secrets Manager (the client secret). `SSM_PREFIX="/marketplace-listing/"`. AWS getters are **lazy + fail-soft** (import never crashes offline). `ledger_store()` → `LocalJsonStore` if `LEDGER_LOCAL_PATH` else `S3JsonStore`. |
| `src/web/auth.py` | `current_user(settings, token)`: returns `dev@local` when `AUTH_DISABLED`, else `verify_jwt` (RS256 pinned; audience = client id; issuer from pool id + region; JWKS looked up by `kid`, cached; jose errors → `AuthError`). **Gotcha:** the Cognito region is taken from `settings.s3_region` (both are `ap-south-1`). |
| `src/web/jobs.py` | Thread-safe in-memory `JobStore` + `Job` dataclass + `STEPS`. Backs the Generate background job + htmx polling. **In-memory only → all jobs are lost on app restart.** |
| `src/web/routers/pages.py` | `GET /` home; `get_user` (reads `id_token` cookie or `Authorization: Bearer`) and `get_settings` helpers reused by other routers. |
| `src/web/routers/generate.py` | Flow A (below). |
| `src/web/routers/fix.py` | Flow B (below); `_safe_fix_id` guards path traversal. |
| `src/web/oauth.py` | Hosted-UI OAuth helpers (`authorize_url`/`exchange_code`/`logout_url`); stdlib urllib, injectable `http` so unit tests never hit the network. |
| `src/web/routers/auth_routes.py` | `GET /login` (state CSRF cookie → hosted UI), `GET /auth/callback` (verify state, exchange code, set `id_token` cookie), `GET /logout`. Sessions are **re-login-on-stale** (no refresh tokens). |

### Routes

| Method + path | Purpose |
|---|---|
| `GET /` | Home / landing. |
| `GET /generate` | Generate form; shows next styleGroupId from the ledger. |
| `POST /generate` | Upload CSV → `reserve()` a batch → spawn background thread → return htmx stepper (header `x-job-id`). |
| `GET /jobs/{job_id}` | htmx poll: returns the stepper while running, the result partial when done/failed. |
| `GET /generate/download/{job_id}` | Download `myntra_filled.xlsx`. |
| `POST /generate/confirm/{job_id}` | `confirm()` the batch → **advances the ledger**. |
| `GET /fix` | Fix form. |
| `POST /fix` | Upload rejection `.xlsx` → classify → persist `rows.json` → return review partial (header `x-fix-id`). |
| `POST /fix/apply/{fix_id}` | Apply typed answers + drop checkboxes → `correct()` → result partial. |
| `GET /fix/download/{fix_id}` | Download `myntra_corrected.xlsx`. |

### Flow A — Generate (request lifecycle)

```
POST /generate (CSV) ─► save to runtime/<job>/ ─► reserve(count) [no advance]
                     └► daemon thread: pipeline.main(...) → set_step()/finish()/fail()
browser htmx-polls GET /jobs/<job> ─► stepper → _result.html
user ─► GET /generate/download/<job>   then   POST /generate/confirm/<job> ─► confirm() advances ledger
```

### Flow B — Fix (request lifecycle)

```
POST /fix (rejection.xlsx) ─► save to runtime/fix-<id>/ ─► read_errors()+classify ─► rows.json
                            └► _fix_review.html (typed free-text inputs + drop checkboxes)
POST /fix/apply/<id> ─► parse answer__<sku>__<field> + drop__<sku> ─► correct() ─► myntra_corrected.xlsx
GET /fix/download/<id>
```

### Templates & static

`templates/`: `base.html` (shell), `home.html`, `generate.html`, `fix.html`, and htmx partials
`_stepper.html`, `_result.html`, `_fix_review.html`, `_fix_result.html`. `static/`: `app.css`
(Marigold Ops theme: warm near-black bg, marigold `#E8A33D` accent), vendored `htmx.min.js`,
and vendored fonts (Space Grotesk / IBM Plex Mono / Inter) — **no runtime CDN**.

### Runtime working dirs — `src/web/runtime/`

Per-request scratch: `runtime/<job_id>/` (Generate: uploaded CSV + outputs) and
`runtime/fix-<id>/` (Fix: `rejection.xlsx`, `rows.json`, `myntra_corrected.xlsx`). Git-ignored
except `.gitkeep`. **Security:** `fix_id` is validated `^[0-9a-f]{32}$` + realpath-contained
inside `runtime/` to prevent path traversal; session rows are JSON (never pickle).

---

## 6. Layer 4 — Cloud / CI-CD / deploy

### Image — `Dockerfile`

`python:3.12-slim`; deps copied before source for layer caching; copies `src config templates
run.py`; `EXPOSE 8080`; `CMD uvicorn src.web.main:app --host 0.0.0.0 --port 8080`.

### Pipeline — `.github/workflows/ci-cd.yml`

Two jobs. **`test`** (on push + PR + dispatch): pytest gate. **`build-and-push`**
(`needs: test`, and `if` event ≠ pull_request **and** ref = `refs/heads/main`): assume the AWS
role via **GitHub OIDC** (no stored keys), ECR login, build, push `:<git-sha>` + `:latest`.
Least-privilege permissions: default `contents: read`; `id-token: write` granted **only** to
`build-and-push`, never to the PR-running `test` job. Repo secret `AWS_ACCOUNT_ID` is just the
account number. **This is CI + image-publish (Continuous Delivery of an artifact), not deploy.**

### AWS policy files

| File | Identity / purpose |
|---|---|
| `aws/cicd/oidc-trust-policy.json` | Trust scoped to `repo:gopalthakur71/…:ref:refs/heads/main`. |
| `aws/cicd/ecr-push-permissions.json` | `GetAuthorizationToken` (`*`) + push actions scoped to the `marketplace-bulklisting` repo ARN. |
| `aws/cicd/ecr-lifecycle-policy.json` | Keep last 10 images. |
| `S3/iam-policy-s3-image-upload.json` | App→S3 upload (PutObject/GetObject on `ijorethnicpartners/myntra/*`). |
| `S3/s3-bucket-policy-ijor-public-read.json` | Anonymous read on `myntra/*` only (rest private). |

### Three separate AWS identities (don't conflate)

1. **App → S3 (local/dev):** an IAM user's access keys via boto3 default chain (S3-only policy).
2. **Pipeline → ECR (CI):** the OIDC role `github-actions-ecr-push` — **no stored keys**.
3. **App on EC2 (deploy):** the instance role `listing-app-ec2-role` — takes over S3 + ECR-pull
   + SSM + Secrets at deploy time, retiring the local keys. (See the deploy runbook.)

### Runtime config & secrets

Non-secret config = **SSM Parameter Store** under `/marketplace-listing/*` (7 params: 3 S3 + 4
Cognito). The one real secret (Cognito client secret) = **Secrets Manager**
`/marketplace-listing/cognito_client_secret`. Rationale (and a leaner alternative) is recorded
in [decisions/2026-06-30-config-ssm-secrets-rationale.md](decisions/2026-06-30-config-ssm-secrets-rationale.md).

### Deploy

Start/stop EC2 t3.micro; a systemd unit pulls `:latest` on boot (**boot = deploy**). Step-by-
step console runbook: [runbooks/web-ec2-deploy-console.md](runbooks/web-ec2-deploy-console.md).
**Deferred:** the `/auth/callback` + `/login` hosted-UI flow is not built yet, so the app
currently runs with `AUTH_DISABLED=1`; building it is the first deploy-phase code task.

---

## 7. Integration boundaries (where external systems plug in)

This is the section to read when something *outside* the code changes.

| Boundary | Where handled | What to know |
|---|---|---|
| **Shopify export (CSV)** | `src/core/shopify_reader.py` | One product = rows sharing a `Handle`; gallery ordered by `Image Position`. A format change here breaks ingestion. |
| **Myntra template (.xlsx)** | `src/myntra/template_reader.py`, `fill.py` | Dropdowns are **x14 extension data-validations** openpyxl drops silently — read from raw `xl/worksheets/*.xml`. Headers row 3 / data row 4 (rejection files). A new template version can shift columns/vocab. |
| **Myntra vocabulary** | `mapper.validate_value` | Dropdown values must match template spelling exactly — flagged, never guessed. |
| **S3 (images + ledger)** | `src/core/s3_upload.py`, `groupid_ledger.S3JsonStore` | Bucket `ijorethnicpartners`, region `ap-south-1`, image prefix `myntra/`, ledger key `state/myntra_groupid.json`. Images must be `.jpg` and public-read. |
| **Cognito (auth)** | `src/web/auth.py`, `settings.py`, `oauth.py`, `auth_routes.py` | Pool/client/domain in SSM; JWT validated by JWKS. Hosted-UI login round-trip (/login → /auth/callback → /logout) built; enable by dropping AUTH_DISABLED. |
| **ECR (image registry)** | `ci-cd.yml`, deploy systemd | Repo `marketplace-bulklisting`, `:latest` pulled on boot. |
| **SSM / Secrets Manager (config)** | `src/web/settings.py` | Per-field env→AWS fallback; prefix `/marketplace-listing/`; read on EC2 via instance role. |

---

## 8. Configuration — `config/myntra/` (edit instead of code)

| File | Controls |
|---|---|
| `column_map.yaml` | Direct Shopify field → Myntra column copies. |
| `constants.yaml` | Fixed per-row values: brand, **manufacturer/packer/importer address with 6-digit pincode**, sizes, AgeGroup, FashionType, Year, Season, mandatory-attribute defaults. |
| `rules.yaml` | Fabric detection (→ fabric/wash-care/HSN), Prominent Colour scan, colour synonyms, `brand_colour_remarks_from_prominent`, **`style_group_id_start`**, Product Details marker. |
| `image_specs.yaml` | Image min dims, max bytes, JPEG quality, max images; **S3 host** (`public_base_url`, `s3_upload`, `s3_bucket`, `s3_region`, `s3_prefix`). |
| `error_rules.yaml` | Maps Myntra error-message substrings → `{category, action, explanation, field}` for the Fix flow. |

---

## 9. Tests — `tests/` (57)

Layers 1–2 in `tests/*.py` (template reader, shopify reader, mapper, images, s3 upload, fill /
inline strings / dropdowns, report, models, config load, end-to-end, **groupid_ledger**,
**error_reader**, **corrector**, **pipeline_override**). Layer 3 in `tests/web/` (settings,
auth, jobs, pages, generate, fix). `python -m pytest -q` is the CI gate.

---

## 10. Docs index

| Path | Role |
|---|---|
| `../AGENTS.md` | Orientation + invariants (entry point). |
| `ARCHITECTURE.md` | This file — map + flow + boundaries. |
| `../README.md` | Usage + Myntra upload rules. |
| `decisions/` | ADRs — *why* (e.g. SSM/Secrets rationale). |
| `runbooks/` | Ops click-throughs: CI/CD, Cognito, SSM/Secrets, EC2 deploy. |
| `superpowers/specs/`, `superpowers/plans/` | Deep design + implementation plans. |
| `journal/` | Day-by-day history incl. the full upload-error debugging chronology. |

> **Keep this current:** when you add a module, an integration, or a layer, update §2 (layout),
> the relevant layer section, and §7 (boundaries). When you make a non-obvious design choice,
> add an ADR under `decisions/`. Stale maps are worse than none.
</content>
