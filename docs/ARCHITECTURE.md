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
| A Myntra attribute is wrong/blank in the output sheet | `src/myntra/mapper.py` + `config/myntra/{rules,constants,column_map}.yaml`. **The 12 name-driving attributes are blank *by design*** — see `user_filled_attributes` in `rules.yaml`. |
| Myntra rejects a value as not in the dropdown | `src/myntra/mapper.py` (`validate_value`) + `src/myntra/template_reader.py` (vocab parse). **Invariant: value must match template spelling.** |
| The downloaded sheet has no dropdowns in Excel | `src/myntra/pipeline.py` (`DEFAULT_TEMPLATE_NAME` must be the V13 plain-validation template) + `src/myntra/fill.py`. Any re-save with openpyxl must also re-run the shared→inline conversion. |
| The Myntra title/description isn't what we expected | Nothing to fix in the sheet — Myntra **generates** them from the attributes. `src/myntra/preview.py` reconstructs them (approximate); see journal 2026-07-24. |
| `Brand Colour (Remarks) cannot be null` on upload | Free-text, mandatory, and **not** filled by the pipeline. Saving via Flow D derives it from Prominent Colour (`attribute_entry.derive_brand_colour`); a sheet filled only in Excel still leaves it blank and Flow B backfills it (`corrector.py`). |
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
                         │  Flow A Generate    Flow B Fix      Flow C Preview   Flow D Fill │
  browser ──────────────►  upload CSV → xlsx  upload rejection upload filled xlsx  attributes│
   (Cognito/AUTH_DISABLED)│        │           → corrected xlsx → listing cards   in-app     │
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
    template_guard.py preview.py attribute_entry.py                # Layer 1
    groupid_ledger.py hsn_kb.py sku_registry.py                    # Layer 2
    error_reader.py corrector.py                                   # Layer 2
  web/                         # Layer 3 (FastAPI app)
    main.py settings.py auth.py jobs.py
    routers/ pages.py generate.py fix.py preview.py attributes.py
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
tests/                         # 222 tests; tests/web/ covers Layer 3
```

---

## 3. Layer 1 — Core fill pipeline

### Data flow (`python run.py`)

```
 Shopify CSV ─┐
              ├─► shopify_reader ─► [Product]                 (group variants + image gallery)
 Myntra xlsx ─┴─► template_reader ─► TemplateInfo            (headers + 37 dropdown vocabularies)
                                          │
                    template_guard.assert_template_compatible(...)   (fail loud on a bad swap)
                                          │
                    mapper.map_product(Product, TemplateInfo, config) ─► MappedRow
                       • constants  • pricing  • HSN  • vocab validation (flag, never guess)
                       • the 12 user_filled_attributes are POPPED → left blank for the seller
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
| `src/myntra/template_reader.py` | Read Myntra template | Detects header/data rows; resolves dropdown vocabularies → `{column → allowed values}`. **Plain `<dataValidation type="list">` first** (the V13 template, which openpyxl preserves), falling back to parsing **x14 extension** validations from raw sheet XML (the old 2026-06-16 template). |
| `src/myntra/template_guard.py` | Fail loud on a template swap | `assert_template_compatible(template, column_map, constants)` raises `TemplateIncompatibleError` if the active template lacks any header the config or pipeline writes (union includes `_PIPELINE_WRITTEN_HEADERS`). Called by `pipeline.main`. |
| `src/myntra/mapper.py` | Map + validate + rules | Constants, pricing, HSN-by-signature, **`validate_value`** (canonicalize to template spelling or flag). **Pops every `user_filled_attributes` header** so the 12 seller-decided attributes are never guessed. Returns `MappedRow`. |
| `src/myntra/fill.py` | Write the Sarees sheet | Numeric cells (`NUMERIC_HEADERS`), S3 image URLs, **clears stray template rows**, **shared→inline strings** (Myntra's parser cannot resolve shared strings), x14 re-injection off by default (`preserve_dropdowns=False`; it breaks Myntra's parser — the V13 template's *plain* validations survive without it). |
| `src/myntra/preview.py` | Reconstruct the Myntra listing | `reconstruct_title` / `reconstruct_design_details` (approximate — Myntra generates these from attributes), `_colour_phrase` / `_colour_display` (Design-Details L1 joins Prominent + Second Prominent Colour; metallics render `-Toned`, per `_TONED_COLOURS`), `missing_attributes`, `read_filled_rows`, **`build_card`** (the one place a listing card is assembled, so Flow C and Flow D can never drift apart). Read-only. |
| `src/myntra/attribute_entry.py` | The seller-decided attributes | `user_filled_attributes()` (reads `rules.yaml` — the single loader), `attribute_vocab(template, columns)` (options **straight from** `vocab_by_header`; nothing added), `validate_submitted` (blank → `None`; non-blank must be an exact vocab member else `AttributeValueError`), `write_attributes(xlsx, template, entries)` (writes into an **already-built** workbook: verifies every row's SKU first, blanks on `None`, then re-applies `fill.shared_to_inline`), **`derive_brand_colour`** (`Brand Colour (Remarks)` = the chosen Prominent Colour, lowercased; `NA`/blank → nothing). Drives Flow D. |
| `src/myntra/report.py` | Audit report | `output/report.txt`: per-SKU filled count, blanks, vocab flags, image pass/fail. |

### The seller-decided attributes (why 12 columns come out blank)

Myntra **ignores the product name and description we submit** and auto-generates the customer-facing
title and the "Design Details" prose **from the attribute columns**. So the attributes *are* the
listing. Getting one wrong publishes a wrong title, and fabric/ornament semantics are a human
judgment ("silk" that is really a blend; solid border vs no border).

**Ground truth from the first Flow-D listing (SKU `164SDE226RPPG`, 2026-07-28)** — the rules the
preview encodes, each verified against the published page:

| Sent | Published | Rule |
|---|---|---|
| `Print or Pattern Type=Striped`, `Saree Fabric=Pure Cotton`, `Type=Khadi` | `Striped Pure Cotton Khadi Saree` | Title word order confirmed; **no colour**, and **no** "With Unstitched Blouse Piece" despite Blouse Fabric being set (`Blouse=NA`). |
| `Prominent=Green`, `Second Prominent=Gold` | `Green and Gold-Toned Khadi sarees` | L1 joins both colours; metallics take `-Toned`. |
| `Border=Solid` | `Striped saree with Woven Design Border border` | Myntra appends **both** "Border" and "border"; the doubling is entirely Myntra's — our vocab words are clean. |
| `Border=Solid` | Specifications showed `Woven Design` | ⚠️ **Myntra can overwrite a submitted attribute.** It runs AI attribute extraction over the product images with catalogue-team review, so specifications are exact only up to Myntra's own correction. |

Decision (2026-07-24, owner): **the machine must not guess them.** `config/myntra/rules.yaml`
lists them under `user_filled_attributes` — the single source of truth, read by the mapper (which
blanks them), by `/preview`, and by the attribute-entry screen:

`Prominent Colour` · `Second Prominent Colour` · `Third Prominent Colour` · `Saree Fabric` ·
`Blouse Fabric` · `Type` · `Ornamentation` · `Border` · `Pattern` · `Print or Pattern Type` ·
`Wash Care` · `Usage`

They are written **blank but with live Excel dropdowns**, which is why the pipeline uses the V13
template (`DEFAULT_TEMPLATE_NAME = Myntra-Sku-Template-2026-07-24.xlsx`): its validations are
plain, and openpyxl preserves plain validations through load→save. The older 2026-06-16 template
stored them as x14 extensions, which openpyxl silently drops — that is why earlier outputs had no
dropdowns.

> ⚠️ **If you ever re-save a built workbook with openpyxl, re-apply `fill.py`'s shared-string →
> inline conversion afterwards.** Myntra's upload parser does not resolve shared strings, and
> openpyxl re-creates them on every save. `fill_template` does this; anything else that writes
> into a finished file must too.

There is no synonym map, no self-learning, and no pre-fill for these columns. Reverse-engineered
title/description rules and the four live listings they came from are in journal 2026-07-24.

---

## 4. Layer 2 — Error-correction backend

Drives the web Fix flow; also usable standalone. No web dependency.

| File | Responsibility | Key details |
|---|---|---|
| `src/myntra/groupid_ledger.py` | styleGroupId counter | `read_ledger`/`reserve`/`confirm` over a pluggable store. **`reserve()` records a pending batch but does NOT advance the counter; only `confirm()` advances** (so an unuploaded batch frees its ids). Store = `LocalJsonStore` (dev file) or `S3JsonStore` (key `state/myntra_groupid.json`). |
| `src/myntra/hsn_kb.py` | HSN knowledge base | `signature(product, category, fabric_keywords)` (shared by the Generate pre-scan and the mapper), `read_kb`/`suggest`/`learn` over the same pluggable store (key `state/hsn_kb.json`, **own local path `HSN_LOCAL_PATH`** — `LocalJsonStore` is one-file-per-path). Learns an 8-digit HSN once per `category\|fabric` signature; seeds from the two ex-`rules.yaml` codes. Suggestion-only — HSN is authoritative **per SKU** (see `sku_registry.py`). HSN is no longer set by the `fabric_detection` block. |
| `src/myntra/sku_registry.py` | Duplicate-generation guard | Per-SKU registry (key `state/sku_registry.json`, **own local path `SKU_REGISTRY_LOCAL_PATH`**) recorded **at generate time**: `content_hash(cells)` (excludes styleGroupId+HSN), `partition(sku_hashes, registry)` → NEW/REPEAT/EDITED, `record(store, sku, hash, style_group_id, hsn)`. On a re-upload the Generate router warns "already generated" and offers a **rebuild-on-demand** download that pins each SKU's stored styleGroupId + HSN (no ledger change). |
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
| `src/web/main.py` | `create_app()`: settings on `app.state` **before** routers; mounts `/static`; includes routers (`pages`, `generate`, `fix`, `auth_routes`, `preview`, `attributes`); maps `AuthError → redirect to /login (HX-Redirect for HTMX)`. Module-level `app` + shared `Jinja2Templates` with an `asset_v()` cache-buster. |
| `src/web/settings.py` | `Settings` dataclass + `load_settings(env, ssm)`: each field resolves **env-first, then per-field fallback** to SSM (the client secret is a SecureString, decrypted via `WithDecryption=True` — no Secrets Manager). `SSM_PREFIX="/marketplace-listing/"`. AWS getter is **lazy + fail-soft** (import never crashes offline) and **logs** failures; values are `.strip()`ed. `ledger_store()` → `LocalJsonStore` if `LEDGER_LOCAL_PATH` else `S3JsonStore`; `hsn_store()` likewise on `HSN_LOCAL_PATH`; `sku_registry_store()` likewise on `SKU_REGISTRY_LOCAL_PATH` (**each a separate path** — `LocalJsonStore` is one-file-per-path). |
| `src/web/auth.py` | `current_user(settings, token)`: returns `dev@local` when `AUTH_DISABLED`, else `verify_jwt` (RS256 pinned; audience = client id; issuer from pool id + region; JWKS looked up by `kid`, cached; jose errors → `AuthError`). **Gotcha:** the Cognito region is taken from `settings.s3_region` (both are `ap-south-1`). |
| `src/web/jobs.py` | Thread-safe in-memory `JobStore` + `Job` dataclass + `STEPS`. Backs the Generate background job + htmx polling. **In-memory only → all jobs are lost on app restart.** |
| `src/web/routers/pages.py` | `GET /` home; `get_user` (reads `id_token` cookie or `Authorization: Bearer`) and `get_settings` helpers reused by other routers. |
| `src/web/routers/generate.py` | Flow A (below); `_safe_job_id` guards path traversal. |
| `src/web/routers/fix.py` | Flow B (below); `_safe_fix_id` guards path traversal. |
| `src/web/routers/preview.py` | Flow C (below): read-only round-trip preview of a filled workbook. Reads `user_filled_attributes` from `rules.yaml`; never modifies the uploaded file. |
| `src/web/routers/attributes.py` | Flow D (below): the in-app **Fill attributes** screen. `job_files(job_id)` locates the job's built workbook + Shopify export (404 `session expired, please re-upload`); `_panels(...)` joins sheet row ↔ SKU ↔ product photo; `_submitted(...)` parses the `attr__{ordinal}__{column_index}` / `sku__{ordinal}` form fields. Writes **only** the 12 attribute cells. |
| `src/web/oauth.py` | Hosted-UI OAuth helpers (`authorize_url`/`exchange_code`/`logout_url`); stdlib urllib, injectable `http` so unit tests never hit the network. |
| `src/web/routers/auth_routes.py` | `GET /login` (state CSRF cookie → hosted UI), `GET /auth/callback` (verify state, exchange code, set `id_token` cookie), `GET /logout`. Sessions are **re-login-on-stale** (no refresh tokens). |

### Routes

| Method + path | Purpose |
|---|---|
| `GET /` | Home / landing. |
| `GET /generate` | Generate form; shows next styleGroupId from the ledger. |
| `POST /generate` | Upload CSV → duplicate-SKU guard → HSN pre-scan → `reserve()` a batch → spawn background thread → return htmx stepper (header `x-job-id`). |
| `POST /generate/hsn/{job_id}` | Submit the one-HSN-per-signature review (8 digits each) → `learn()` into the KB → start the build. |
| `POST /generate/new-only/{job_id}` | Duplicate guard: build only the NEW + EDITED SKUs. |
| `POST /generate/continue/{job_id}` | Duplicate guard override ("Continue anyway"): rebuild EVERY SKU in the file. SKUs the registry already knows (repeat + edited) are pinned back to their stored styleGroupId so a rework stays in the same Myntra style group; new SKUs draw from the ledger. HSN is re-asked via the normal pre-scan. |
| `GET /generate/rebuild/{job_id}` | Duplicate guard: rebuild the REPEAT SKUs, pinning their stored styleGroupId + HSN (no ledger change). |
| `GET /jobs/{job_id}` | htmx poll: returns the stepper while running, `_cancelled.html` when stopped, the result partial when done/failed. Every one of those carries the `#run-controls` OOB fragment, so Stop appears and disappears with the run. |
| `POST /generate/cancel/{job_id}` | Stop a running build: sets the job's `cancel_requested` flag and returns the stepper immediately ("Stopping…"). The worker lands it at its next checkpoint — see the cancellation note below. |
| `GET /generate/download/{job_id}` | Download `myntra_filled.xlsx`. |
| `POST /generate/confirm/{job_id}` | `confirm()` the batch → **advances the ledger**. |
| `POST /generate/unconfirm/{job_id}` | Undo a mark-as-uploaded (refuses if a later batch was confirmed). |
| `POST /generate/style-start` + `/undo` | Seed the ledger from the last styleGroupId already used on Myntra. |
| `GET /generate/attributes/{job_id}` | Flow D form: one accordion panel per SKU — product photo, the 12 vocabulary-only dropdowns (pre-selected from the workbook), an `n/12 filled` counter, the read-only derived `Brand Colour (Remarks)`, and the current listing card. |
| `POST /generate/attributes/{job_id}/preview` | htmx fragment: re-renders **one** listing card from the posted dropdown values via the same `build_card`. Touches no file. |
| `POST /generate/attributes/{job_id}` | Save: validate every value against the template vocabulary, then write all SKUs' attributes into the built workbook. Returns a 200 panel on success **and** on validation failure (htmx-swappable, never a 500); an off-vocab value writes nothing at all. |
| `GET /preview` | Preview form (Flow C). |
| `POST /preview` | Upload the **filled** `.xlsx` → one listing card per row: exact specifications + labelled-approximate title/Design Details + "not filled" flags. Read-only. |
| `GET /fix` | Fix form. |
| `POST /fix` | Upload a rejection file (**3 formats:** per-SKU `.xlsx`, file-level `.csv`, or MDirect Listings Report) → detect format → classify → persist `rows.json` → return review partial (header `x-fix-id`) split into **correctable** vs **explain_only** groups. |
| `POST /fix/apply/{fix_id}` | Two submit actions from `_fix_review.html`: **`action=fix`** applies typed answers + drop checkboxes → `correct()` → corrected sheet of *only the correctable* SKUs ("Download now to fix"); **`action=manual`** rebuilds a fresh sheet for *only the explain_only* SKUs from an uploaded Shopify export, pinning their original HSN + styleGroupId ("Download listing file"). Surface-B correctable rebuilds and every manual rebuild need `products_export` (`needs_export`); the whole handler is wrapped so any error returns a 200 error panel, never a swallowed 500. |
| `GET /fix/download/{fix_id}` | Download the rebuilt `.xlsx`. |

### Flow A — Generate (request lifecycle)

```
POST /generate (CSV) ─► save to runtime/<job>/ ─► dedup guard ─► HSN pre-scan/review
                     ─► reserve(count) [no advance]
                     └► daemon thread: pipeline.main(...) → set_step()/finish()/fail()
browser htmx-polls GET /jobs/<job> ─► stepper → _result.html
user ─► GET /generate/download/<job>   then   POST /generate/confirm/<job> ─► confirm() advances ledger
```

**Stopping a run.** `POST /generate/cancel/<job>` sets `Job.cancel_requested`; the worker
passes `should_cancel` into `pipeline.main`, which checks it **between whole products**,
before `fill_template`, and before the S3 upload, then raises `BuildCancelled`.
Cancellation is deliberately cooperative and bounded by one product — killing the thread
mid-write would risk a corrupt workbook and a half-uploaded image set.

`_land_cancelled` then leaves no trace: the part-written `myntra_filled.xlsx` is deleted
and `groupid_ledger.cancel()` marks the reserved batch `cancelled`. **No ids are burned**
— `reserve()` never advanced the counter (invariant 3) — and no SKUs are registered,
because `record()` only runs after `pipeline.main` returns. A stopped run is therefore
safe to simply repeat.

`should_cancel` defaults to `None`, which keeps the CLI path and every other caller of
`pipeline.main` unchanged.

### Flow C — Preview (request lifecycle)

```
GET /generate/download/<job> ─► seller fills the 12 attribute dropdowns in Excel ─► saves
POST /preview (filled .xlsx) ─► temp file ─► read_template(V13) ─► preview.read_filled_rows()
                             └► _preview.html: one card per SKU
                                • Specifications  = EXACT (what the seller entered)
                                • Title / Design Details = APPROXIMATE, badged
                                  (Myntra generates them; we reconstruct)
                                • "Not filled" flags for blank attributes
seller ─► uploads the SAME file to Myntra   (the app never modifies it)
```

### Flow D — Fill attributes in-app (request lifecycle)

Additive: Flow C's Excel round-trip still works untouched. Filling here is **optional**.

```
_result.html "✎ Fill attributes" ─► GET /generate/attributes/<job>
    job_files() → runtime/<job>/{myntra_filled.xlsx, products_export.csv}
    read_filled_rows(xlsx) ─┬─ row ordinal ↔ vendorSkuCode ↔ Product.images[0]  (photo)
                            └─ attributes already in the sheet → pre-selected options
    attributes.html: one <details> panel per SKU (12 selects, options = vocab_by_header only)

on every dropdown change ─► POST /generate/attributes/<job>/preview  (hx-include closest panel)
                         └► preview.build_card(posted values) ─► _preview_card.html fragment
                            (the SAME reconstruction /preview uses — no JS logic duplicate)

"Save attributes" ─► POST /generate/attributes/<job>
    validate_submitted(values, vocab)   → off-vocab ⇒ 200 error panel, NOTHING written
    derive_brand_colour(values)         → Brand Colour (Remarks) = colour.lower()  (13th cell)
    write_attributes(xlsx, ...)         → row SKUs verified, cells written, blanks cleared
                                        → fill.shared_to_inline() RE-APPLIED  (see below)
GET /generate/download/<job> ─► the same file, now with the chosen attributes AND live dropdowns
```

**The invariant that bites:** openpyxl re-creates shared strings on every save, and Myntra's
upload parser cannot resolve them. Any code path that re-saves a built workbook **must** call
`fill.shared_to_inline(path, fill.sheet_xml_name(path, "Sarees"))` afterwards, or Myntra rejects
the file. Locked by `test_write_attributes_keeps_strings_inline` (asserts no `t="s"` remains).

### Flow B — Fix (request lifecycle)

```
POST /fix (rejection file) ─► save to runtime/fix-<id>/ ─► detect_format ─► read_errors()+classify ─► rows.json
                            └► _fix_review.html: shared products_export upload (if needs_export) at top,
                               then "We can fix these" (correctable: typed inputs + drop checkboxes +
                               "Download now to fix" action=fix), then "You must fix these yourself first"
                               (explain_only + guidance + "Download listing file" action=manual)
POST /fix/apply/<id> ─► action=fix : correct() correctable SKUs ────────────┐
                     └► action=manual: rebuild explain_only SKUs from ───────┤► rebuilt .xlsx
                        products_export, pinning original HSN + styleGroupId ┘
GET /fix/download/<id>
```

### Templates & static

`templates/`: `base.html` (shell), `home.html`, `generate.html`, `fix.html`, `preview.html`,
`attributes.html`, and htmx partials `_stepper.html`, `_result.html`, `_confirmed.html`,
`_mark_upload.html`, `_dedup_warn.html`, `_hsn_review.html`, `_style_start.html`,
`_cancelled.html`, `_run_controls.html`, `_fix_review.html`, `_fix_result.html`,
`_preview.html`, `_preview_card.html` (the one card
markup, shared by Flows C and D), `_attr_panel.html`, `_attr_saved.html`. `static/`: `app.css`
(Marigold Ops theme: warm near-black bg, marigold `#E8A33D` accent), vendored `htmx.min.js`,
and vendored fonts (Space Grotesk / IBM Plex Mono / Inter) — **no runtime CDN**.

`_run_controls.html` is the only **out-of-band** partial: every Generate-flow response
swaps it into the `#run-controls` slot beside the Generate button, filled with Stop while
a build runs and empty once it ends. Riding the stepper's existing 1-second poll makes it
self-healing — a missed swap corrects itself on the next tick. Note the button carries
`hx-params="none"`: it sits inside the upload form, and htmx would otherwise post the
form's values (including the CSV, under the inherited multipart encoding) on every click.

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
   + SSM (config incl. the SecureString secret) at deploy time, retiring the local keys, plus
   `AmazonSSMManagedInstanceCore` so CI can redeploy. (See the deploy runbook.)

### Runtime config & secrets

All runtime config = **SSM Parameter Store** under `/marketplace-listing/*` (8 params: 3 S3 + 4
Cognito + the Cognito client secret as a **SecureString**). **No Secrets Manager** — it was
retired 2026-07-02 (SSM SecureString is free and read the same way, with `WithDecryption=True`).
Rationale in [decisions/2026-06-30-config-ssm-secrets-rationale.md](decisions/2026-06-30-config-ssm-secrets-rationale.md).

### Deploy

Start/stop EC2 t3.micro; a systemd unit pulls `:latest` on boot (**boot = deploy**), and CI's
`deploy` job restarts it via SSM Run Command on every push to `main` (full CD). Real Cognito auth
is **live** (reached via SSH tunnel to localhost; no TLS yet). Step-by-step console runbook:
[runbooks/web-ec2-deploy-console.md](runbooks/web-ec2-deploy-console.md); full resource map:
[infra-resources.md](infra-resources.md).

---

## 7. Integration boundaries (where external systems plug in)

This is the section to read when something *outside* the code changes.

| Boundary | Where handled | What to know |
|---|---|---|
| **Shopify export (CSV)** | `src/core/shopify_reader.py` | One product = rows sharing a `Handle`; gallery ordered by `Image Position`. A format change here breaks ingestion. |
| **Myntra template (.xlsx)** | `src/myntra/template_reader.py`, `template_guard.py`, `fill.py` | Active template = V13 (`DEFAULT_TEMPLATE_NAME`), whose dropdowns are **plain data-validations openpyxl preserves**; the older template's **x14 extension** validations are dropped on save and are read from raw `xl/worksheets/*.xml` instead. Headers row 3 / data row 4. A new template version can shift columns/vocab — `template_guard` fails the run loudly rather than silently dropping a column. |
| **Myntra vocabulary** | `mapper.validate_value`, `template_reader.vocab_by_header` | Dropdown values must match template spelling exactly — flagged, never guessed. The seller-facing dropdowns offer **only** these values; nothing (not even `NA`) is ever added to a list. |
| **Myntra's generated title/description** | `src/myntra/preview.py` | Myntra derives them from the attributes and ignores what we submit. The reconstruction is deliberately labelled approximate — do not try to pixel-match it. Both preview surfaces go through `build_card`, so change it in one place. |
| **S3 (images + ledger)** | `src/core/s3_upload.py`, `groupid_ledger.S3JsonStore` | Bucket `ijorethnicpartners`, region `ap-south-1`, image prefix `myntra/`, ledger key `state/myntra_groupid.json`. Images must be `.jpg` and public-read. |
| **Cognito (auth)** | `src/web/auth.py`, `settings.py`, `oauth.py`, `auth_routes.py` | Pool/client/domain + client secret (SecureString) in SSM; JWT validated by JWKS with `verify_at_hash: False` (Cognito id_tokens carry `at_hash`). Hosted-UI login round-trip (/login → /auth/callback → /logout) is **live**. |
| **ECR (image registry)** | `ci-cd.yml`, deploy systemd | Repo `marketplace-bulklisting`, `:latest` pulled on boot; CI `deploy` job restarts via SSM. |
| **SSM Parameter Store (config)** | `src/web/settings.py` | Per-field env→SSM fallback; prefix `/marketplace-listing/`; read on EC2 via instance role. Secret is a SecureString. No Secrets Manager. |

---

## 8. Configuration — `config/myntra/` (edit instead of code)

| File | Controls |
|---|---|
| `column_map.yaml` | Direct Shopify field → Myntra column copies. |
| `constants.yaml` | Fixed per-row values: brand, **manufacturer/packer/importer address with 6-digit pincode**, sizes, AgeGroup, FashionType, Year, Season, mandatory-attribute defaults. |
| `rules.yaml` | **`user_filled_attributes`** (the 12 seller-decided columns the mapper blanks — the single source of truth, read by `/preview` and the Fill-attributes screen too), fabric keyword detection (feeds the HSN signature only), `replicate_constant_across_numbered`, **`style_group_id_start`**, Product Details marker. |
| `image_specs.yaml` | Image min dims, max bytes, JPEG quality, max images; **S3 host** (`public_base_url`, `s3_upload`, `s3_bucket`, `s3_region`, `s3_prefix`). |
| `error_rules.yaml` | Maps Myntra error-message substrings → `{category, action, explanation, field}` for the Fix flow. |

---

## 9. Tests — `tests/` (222)

Layers 1–2 in `tests/*.py` (template reader, shopify reader, mapper, images, s3 upload, fill /
inline strings / dropdowns, report, models, config load, end-to-end, **groupid_ledger**,
**hsn_kb**, **sku_registry**, **error_reader**, **corrector**, **pipeline_override**,
**template_guard**, **preview**, **attribute_entry**). Layer 3 in `tests/web/` (settings, auth,
jobs, pages, generate, fix, preview, **attributes**, and a real-pipeline fix e2e).
`python -m pytest -q` is the CI gate.

Two of them guard invariants that fail *silently* if broken — do not delete them to make a
refactor pass: `test_write_attributes_keeps_strings_inline` (no `t="s"` cell survives an in-app
save) and `test_save_keeps_dropdowns_alive_in_the_downloaded_file` (the owner's Excel check).

Note: `tests/web/test_attributes.py` is slow (~4½ min) because every request re-reads the V13
template (~5 s), the same cost `/preview` already pays. Correct, just unhurried.

---

## 10. Docs index

| Path | Role |
|---|---|
| `../AGENTS.md` | Orientation + invariants (entry point). |
| `ARCHITECTURE.md` | This file — map + flow + boundaries. |
| `../README.md` | Usage + Myntra upload rules. |
| `APP-FEATURES-GUIDE.md` | Plain-English tour of **every user-facing feature** (non-technical). |
| `TECH-EXPLAINED-FOR-BEGINNERS.md` | Plain-English tour of **every technology** used (zero prior tech knowledge). |
| `decisions/` | ADRs — *why* (e.g. SSM/Secrets rationale). |
| `runbooks/` | Ops click-throughs: CI/CD, Cognito, SSM/Secrets, EC2 deploy. |
| `superpowers/specs/`, `superpowers/plans/` | Deep design + implementation plans. |
| `journal/` | Day-by-day history incl. the full upload-error debugging chronology. |

> **Keep this current:** when you add a module, an integration, or a layer, update §2 (layout),
> the relevant layer section, and §7 (boundaries). When you make a non-obvious design choice,
> add an ADR under `decisions/`. Stale maps are worse than none.
</content>
