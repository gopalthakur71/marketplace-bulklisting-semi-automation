# Design — Listing Web App + Cloud/CI-CD Deployment

Date: 2026-06-25
Status: approved (brainstorm) → next: implementation plan

## 1. Purpose & context

The deterministic Shopify → Myntra listing pipeline (`src/core` shared, `src/myntra`
specific, driven by `python run.py`) works end-to-end and is on GitHub
(`gopalthakur71/marketplace-bulklisting-semi-automation`). It is currently a CLI that
a technical user runs locally.

This project wraps that pipeline in a **beautiful but simple web UI** so non-technical
team members can use it, and ships it via a **full cloud + CI/CD setup** on AWS. The
explicit goals:

- Non-technical colleagues generate Myntra upload sheets without touching code, Python,
  or AWS credentials.
- Colleagues can **upload Myntra error files and get plain-language fixes** (the
  hardest part for a non-technical user) — interactive correction (option C).
- Showcase **cloud + CI/CD** experience (the UI is intentionally simple so the cloud
  story is the centrepiece). The frontend uses the `frontend-design` skill at build
  time to look sharp without a heavy JS stack.

Non-goals / explicitly deferred: see §11.

## 2. Architecture overview

```
Browser ──Cognito login──▶ FastAPI app (Jinja + Tailwind + htmx) in ONE Docker container
                              running on a start/stop EC2 t3.micro (ap-south-1)
   config  ← SSM Parameter Store (bucket, region, prefix, Cognito IDs)
   secret  ← Secrets Manager (Cognito client secret)
   images  → S3   (ijorethnicpartners/myntra/<sku>/<n>.jpg)
   ledger  ↔ S3   (ijorethnicpartners/state/myntra_groupid.json)
                              ── ALL AWS access via the EC2 instance IAM role (no static keys)
   output  → myntra_filled.xlsx (download)

CI/CD: GitHub Actions on push to main → run pytest → build image →
       authenticate to ECR via GitHub OIDC (no long-lived AWS keys) → push tagged image.
EC2 on boot: systemd unit pulls the latest image from ECR and runs it
       (start-on-demand == always deploying the latest build).
```

**Compute decision:** an always-on (start/stop) EC2 t3.micro, not Lambda. Rationale:
low-traffic internal tool used occasionally; avoids Lambda's 15-min limit, API Gateway's
29-s cap, and async/cold-start complexity. The same container can move to Lambda later
if usage becomes spiky. Idle cost ≈ the EBS volume only (~$0.5–0.75/mo); no Elastic IP
(use the auto-assigned public IP, which changes per start).

## 3. Repository placement (reuses existing structure)

- **New** `src/web/` — presentation layer (FastAPI app, routers, templates, static).
  Marketplace-agnostic; dispatches to a marketplace pipeline.
- **Reused unchanged:** `src/core/` (models, shopify_reader, images, s3_upload) and most
  of `src/myntra/` (template_reader, mapper, fill, report, pipeline).
- **New `src/myntra/` modules:** `error_reader.py`, `corrector.py`, `groupid_ledger.py`.
- **New config:** `config/myntra/error_rules.yaml`.
- **New deploy artifacts:** `Dockerfile`, `.github/workflows/ci-cd.yml`, `infra/`
  (EC2 user-data, IAM role policy docs, SSM parameter list).
- **Small pipeline change:** `pipeline.main()` gains an optional
  `style_group_id_start` override (used by the web layer from the ledger) and returns
  enough detail for the UI (assigned range, report object).

## 4. App / UI flow

All pages require a valid Cognito session. Two flows behind a shared nav:

### Flow A — Generate
1. **Login** — Cognito hosted UI redirect → app stores the validated session.
2. **Generate** — upload Shopify CSV; marketplace = Myntra (dropdown, only Myntra
   enabled now); the styleGroupId start is read from the **ledger** (not typed). Submit.
3. **Progress** — htmx polls a job-status endpoint; shows live steps (reading → mapping
   → converting+uploading images → writing sheet) with counts.
4. **Result** — download `myntra_filled.xlsx`; inline report (per-SKU summary, vocab
   flags, blanks, image pass/fail); shows the **assigned styleGroupId range** and a
   **"Mark this upload as successful"** button (commits the ledger — see §6).

### Flow B — Fix Myntra errors (interactive, option C)
1. **Upload** the Myntra resubmission file (`.xlsx` with `STATUS` /
   `SYSTEM ERROR MESSAGE`).
2. **Classify** each row's errors via the knowledge base (§5):
   - **Auto-fixable** (deterministic) → applied, shown as "fixed" (e.g. already-listed →
     drop SKU; pincode / numeric / colour-synonym / styleGroupId → re-applied).
   - **Needs you** (manual) → inline form with choices from the template's real dropdown
     vocab (e.g. "Myntra has no 'Ivory' for 78SAZ — pick White / Cream / Off White").
   - **Explain-only** (account/brand) → plain-English text + recommended action.
3. **Review & submit** — every change shown in plain language; user answers manual
   prompts and chooses which already-listed SKUs to drop.
4. **Regenerate** → corrected `myntra_filled.xlsx` + a change summary.

The Fix flow is **self-contained**: it operates on the error file's own data rows (which
contain everything submitted, including S3 image URLs), so the user need not re-supply
the Shopify CSV.

## 5. Error knowledge base (config-driven)

`config/myntra/error_rules.yaml` maps a `SYSTEM ERROR MESSAGE` substring/pattern to:
`category`, plain-English `explanation`, and `action`
(`auto_fix` | `manual_choice` | `drop_sku` | `explain_only`). Examples to seed from real
errors already captured in `errors/myntra/`:

| Pattern (substring) | Category | Action |
|---|---|---|
| `already registered` | duplicate | drop_sku |
| `Pincode is missing` | pincode | auto_fix (from constants) |
| `MRP ... non numeric` / `ISP cannot be empty` | pricing/numeric | auto_fix |
| `extension is not jpg/jpeg` | image | auto_fix (S3 `.jpg` URLs) |
| `Brand Colour (Remarks) cannot be null` / not in dropdown | vocab | manual_choice |
| `StyleGroupId` / `Style SKU Count` | stylegroupid | auto_fix (ledger) |
| (unmatched) | unknown | explain_only (show raw message + generic guidance) |

`src/myntra/error_reader.py` parses the file → list of per-SKU issues.
`src/myntra/corrector.py` applies auto-fixes + user answers, drops excluded SKUs, and
regenerates via the existing `fill.py` sheet-writer (reusing `template_reader` vocab for
manual choices).

## 6. styleGroupId ledger (S3-backed JSON, no DB)

**Why JSON-in-S3, not local or SQLite:** the container is ephemeral (pulled fresh from
ECR on every boot), so local state is lost on redeploy/restart. A JSON object in S3
persists across restarts/redeploys/stop-start, is readable/writable via the instance
role, and needs no database.

**File:** `s3://ijorethnicpartners/state/myntra_groupid.json`
```json
{
  "next_style_group_id": 16,
  "batches": [
    {"id": "...", "file": "myntra_filled_...xlsx", "range": [11, 15],
     "status": "confirmed", "at": "2026-06-25T..."}
  ]
}
```

**`src/myntra/groupid_ledger.py`:** `read()`, `reserve(count) -> (start, batch_id)`,
`confirm(batch_id)`, `list_pending()`. Generate reserves a range and records the batch as
`pending` **without** advancing the counter. The Result screen's **"Mark upload
successful"** button calls `confirm()`, which advances `next_style_group_id` past the
range and flips the batch to `confirmed`. A failed upload is simply never confirmed, so
its IDs free up for reuse. Seeded once with the current next ID (~16).

**Documented limitation & upgrade path:** assumes one batch is confirmed before the next
is generated (matches the team's workflow). Simultaneous generation by two users could
reserve the same IDs. Accepted for a trusted team. **If this is ever taken multi-tenant /
SaaS, replace the JSON ledger with a database** (atomic counter) — that is the explicit
trigger for adopting a DB; until then, JSON suffices.

## 7. Auth, config & secrets

- **Cognito** user pool; FastAPI validates the Cognito-issued JWT on every request
  (hosted-UI redirect login). You add/remove colleagues in Cognito.
- **EC2 instance IAM role** (no static keys in the container) granting least privilege:
  `s3:PutObject/GetObject` on `ijorethnicpartners/myntra/*`, `s3:GetObject/PutObject` on
  `ijorethnicpartners/state/myntra_groupid.json`, `ssm:GetParameter*` on the app's
  parameter path, `secretsmanager:GetSecretValue` on the Cognito secret.
- **SSM Parameter Store:** non-secret config (bucket, region, prefix, Cognito user-pool
  & client IDs). **Secrets Manager:** the Cognito client secret. App loads them at
  startup.

## 8. Generation/job execution (no extra infra)

Upload → an in-process background task runs `pipeline.main()` (or the corrector) against a
per-job temp dir + the uploaded file → progress tracked in an **in-memory job store** →
htmx polls status → result file served for download → temp cleaned afterwards. No Celery,
no Redis, no DB. Single container, stateless between runs (the only durable state is the
S3 ledger).

## 9. CI/CD (the showcase)

`.github/workflows/ci-cd.yml`, triggered on push to `main`:
1. Checkout, set up Python, install deps.
2. **Run the pytest suite** (gate — currently 29 tests + new web/error/ledger tests).
3. Build the Docker image (lint/optional).
4. **Authenticate to AWS via GitHub OIDC** (no long-lived AWS keys stored in GitHub) →
   push the image to **ECR**, tagged with the commit SHA + `latest`.

Deployment: EC2 **user-data + a systemd unit** that, on boot, logs in to ECR (instance
role), pulls `:latest`, and runs the container. Because the box is start-on-demand,
starting it = deploying the newest build. (Later hardening, out of MVP scope: a GitHub
Actions step issuing an SSM Run Command to redeploy a *running* instance.)

## 10. Testing

- **Reuse** the existing suite (self-contained via `tests/fixtures/products_export.csv`).
- **Add:** `error_reader` (parse real captured error files → expected issues),
  `corrector` (auto-fix + manual-answer → corrected rows; drop already-listed),
  `groupid_ledger` (reserve/confirm/reuse with a stubbed S3 client), and web-layer tests
  (upload validation, job lifecycle, auth gate) with the pipeline stubbed.
- CI runs all tests as the deploy gate.

## 11. Out of scope (YAGNI)

No database (S3 JSON ledger instead — DB only on SaaS/multi-tenant), no React/SPA, no
Lambda, no autoscaling/load balancer, no per-user history/audit beyond the ledger, no
Myntra API integration (uploads stay manual via the Myntra portal), no marketplaces other
than Myntra (structure already supports adding them). Concurrency guard on the ledger
deferred (see §6).

## 12. Components summary (interfaces & dependencies)

| Unit | Does | Depends on |
|---|---|---|
| `src/web/` (FastAPI) | Serves UI, auth gate, job orchestration, downloads | `src.myntra.pipeline`, `error_reader`, `corrector`, `groupid_ledger`, Cognito, SSM/Secrets |
| `src/myntra/error_reader.py` | Parse resubmission file → per-SKU issues | `error_rules.yaml`, openpyxl |
| `src/myntra/corrector.py` | Apply auto-fixes + user input, regenerate sheet | `error_reader`, `template_reader`, `fill` |
| `src/myntra/groupid_ledger.py` | Reserve/confirm styleGroupIds in S3 JSON | boto3 (instance role) |
| `config/myntra/error_rules.yaml` | Error pattern → explanation + action | — |
| `Dockerfile` | One-container image of app + pipeline | — |
| `.github/workflows/ci-cd.yml` | Test → build → push to ECR (OIDC) | ECR, GitHub OIDC |
| `infra/` | EC2 user-data, IAM role policy, SSM params | — |
