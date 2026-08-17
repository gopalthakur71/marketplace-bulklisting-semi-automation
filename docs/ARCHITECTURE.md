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
| Replacement image not accepted / "not configured" error | `src/myntra/image_replace.py` (`prepare`/`host`) + `config/myntra/image_specs.yaml`. `host()` raises `ImageConfigError` rather than write a local path — check `public_base_url`/`s3_bucket`/`s3_upload`. |
| A corrected photo still shows the rejected one on Myntra | Caching, not a bug — the replacement key is content-addressed (`{sku}/{slot}-{hash}.jpg`) precisely so a genuinely different photo gets a new URL; re-check the file you actually uploaded. See "Image replacement" in §3. |
| Preview screen won't open an uploaded file / "no products" | `src/web/routers/preview.py` (`preview_submit`) — refuses before adopting if `read_filled_rows` finds no rows. |
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
   (Cognito/AUTH_DISABLED)│        │           → corrected xlsx → adopts, hands  + images    │
                         │        │                             off to Flow D    in-app     │
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
    template_guard.py preview.py attribute_entry.py hsn_source.py  # Layer 1
    image_replace.py                                               # Layer 1
    groupid_ledger.py sku_registry.py                              # Layer 2
    hsn_kb.py                      # Layer 2, RETAINED BUT NOT WIRED (see its docstring)
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
                       • constants  • pricing  • HSN (from the export)  • vocab validation
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
| `src/myntra/mapper.py` | Map + validate + rules | Constants, pricing, HSN (`hsn=` the export's normalised code, `hsn_override=` a pinned per-SKU code that always wins), **`validate_value`** (canonicalize to template spelling or flag). **Pops every `user_filled_attributes` header** so the 12 seller-decided attributes are never guessed. Returns `MappedRow`. |
| `src/myntra/fill.py` | Write the Sarees sheet | Numeric cells (`NUMERIC_HEADERS`), S3 image URLs, **clears stray template rows**, **shared→inline strings** (Myntra's parser cannot resolve shared strings), x14 re-injection off by default (`preserve_dropdowns=False`; it breaks Myntra's parser — the V13 template's *plain* validations survive without it). |
| `src/myntra/preview.py` | Reconstruct the Myntra listing | `reconstruct_title` / `reconstruct_design_details` (approximate — Myntra generates these from attributes), `_colour_phrase` / `_colour_display` (Design-Details L1 joins Prominent + Second Prominent Colour; metallics render `-Toned`, per `_TONED_COLOURS`), `missing_attributes`, `read_filled_rows`, **`build_card`** (the one place a listing card is assembled, so Flow C and Flow D can never drift apart). Read-only. |
| `src/myntra/attribute_entry.py` | The seller-decided attributes | `user_filled_attributes()` (reads `rules.yaml` — the single loader), `attribute_vocab(template, columns)` (options **straight from** `vocab_by_header`; nothing added), `validate_submitted` (blank → `None`; non-blank must be an exact vocab member else `AttributeValueError`), `write_attributes(xlsx, template, entries)` (writes into an **already-built** workbook: verifies every row's SKU first, blanks on `None`, then re-applies `fill.shared_to_inline`), **`derive_brand_colour`** (`Brand Colour (Remarks)` = the chosen Prominent Colour, lowercased; `NA`/blank → nothing), **`validate_hsn`** (blank clears the cell; a non-blank value must pass `hsn_source.normalize`, else `AttributeValueError` — a bad code *typed here* is a mistake worth showing, unlike one merely absent from the export). Drives Flow D. |
| `src/myntra/hsn_source.py` | The one definition of "a usable HSN" | `normalize(raw)` → the stripped 8-digit code or `None`. Pure — no web, jobs, or Shopify knowledge — so the build and the attribute screen enforce one rule from one place. An unusable value is a **gap to fill on the attribute screen**, never a mid-build crash, which is why it returns rather than raises. |
| `src/myntra/image_replace.py` | Replace one product's image after a rejection | Pure image prep + hosting, no web-framework imports — separate from `core/images.py` because the source differs (browser-uploaded bytes, not a Shopify URL fetched during a build); shares the conversion/validation code rather than duplicating it. `load_specs()` reads `image_specs.yaml`. `prepare(sku, slot, data, specs, out_dir)` converts+validates **one** file and **never raises** — a bad photo, an unsafe SKU, or a filesystem error comes back as a per-slot reason string, so one bad file in a batch of seven costs only that slot. `host(prepared, specs, out_dir)` uploads the prepared JPGs and returns their public URLs, raising `ImageConfigError` if S3 hosting isn't configured (no silent fallback to a local path Myntra could never fetch). `replacement_key(sku, slot, data)` builds the S3 key `{sku}/{slot}-{hash}.jpg`, hashing the file's own bytes — see "Image replacement" below for why. Validates `sku` against `_SAFE_SKU = [A-Za-z0-9_-]{1,64}` (no dots — a bare `..` would otherwise traverse one directory level) before it ever reaches a filesystem path or S3 key. |
| `src/myntra/hsn_kb.py` | **RETAINED BUT NOT WIRED IN** | The old learn-once-per-`category\|fabric` knowledge base. Nothing on the request path imports it; kept with its tests green as a fallback. Retired because the signature is too coarse — dhonkhali and katthai are both `saree\|cotton` yet need different codes. Do not send new work here. |
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

### Image replacement (why the S3 key is content-addressed)

The build path writes each image as `{sku}/{n}.jpg`. A **replacement** photo (uploaded from the
Preview/Fill-attributes screen after Myntra rejects one) is written as `{sku}/{slot}-{hash}.jpg`
instead, where the hash is the SHA-256 of the file's own bytes (`image_replace.replacement_key`).

This matters because Myntra, browsers, and any CDN in front of the bucket cache by **URL**. If a
corrected photo were uploaded to the *same* URL as the rejected one, a cached copy could keep being
served and the correction would silently not take — there is no reliable way from this side to force
a re-fetch of an unchanged URL. Deriving the filename from the image's own content sidesteps the
problem entirely: every distinct photo gets a distinct URL, so a replacement is always fetched fresh,
while uploading the identical file twice is idempotent (same bytes → same hash → same key, no
duplicate object).

`image_replace.prepare()` never raises — a bad photo, a filesystem error, or an unsafe SKU comes back
as a reason string scoped to that one slot, so one bad file among several submitted together fails
only its own slot; the others still land. `image_replace.host()` raises `ImageConfigError` if S3
hosting isn't configured, rather than falling back to a local path Myntra could never fetch.

---

## 4. Layer 2 — Error-correction backend

Drives the web Fix flow; also usable standalone. No web dependency.

| File | Responsibility | Key details |
|---|---|---|
| `src/myntra/groupid_ledger.py` | styleGroupId counter | `read_ledger`/`reserve`/`confirm` over a pluggable store. **`reserve()` records a pending batch but does NOT advance the counter; only `confirm()` advances** (so an unuploaded batch frees its ids). Store = `LocalJsonStore` (dev file) or `S3JsonStore` (key `state/myntra_groupid.json`). |
| `src/myntra/hsn_kb.py` | **RETAINED BUT NOT WIRED IN** — see the table in §3 | `signature`/`read_kb`/`suggest`/`learn` over a pluggable store (key `state/hsn_kb.json`, **own local path `HSN_LOCAL_PATH`**). Nothing on the request path imports it. HSN now comes from the export's `custom.hsn_code` metafield via `hsn_source.normalize`, and is corrected per SKU on the Fill-attributes screen. `settings.hsn_store` and `HSN_LOCAL_PATH` remain for it. |
| `src/myntra/sku_registry.py` | Duplicate-generation guard | Per-SKU registry (key `state/sku_registry.json`, **own local path `SKU_REGISTRY_LOCAL_PATH`**) recorded **at generate time**: `content_hash(cells)` (excludes styleGroupId+HSN), `partition(sku_hashes, registry)` → NEW/REPEAT/EDITED, `record(store, sku, hash, style_group_id, hsn)`. On a re-upload the Generate router warns "already generated" and offers a **rebuild-on-demand** download that pins each SKU's stored styleGroupId + HSN + names (no ledger change). `update_names(store, sku, names)` pins the hand-written `List View Name` / `productDisplayName` (merge per key; a blank value REMOVES the pin rather than storing blank) **beside** the fingerprint, never inside it — pinning must not make an unchanged SKU look edited. Consumed as `pipeline.main(names_by_sku=...)` → `mapper.map_product(names_override=...)`, applied last so it beats the title-derived `productDisplayName`. |
| `src/myntra/error_reader.py` | Read + classify rejections | Reads the Myntra rejection `.xlsx` (headers row 3, data row 4; error cols `STATUS`, `SYSTEM ERROR MESSAGE`); splits the message on `;` and **classifies each issue via `config/myntra/error_rules.yaml`** into a `{category, action, explanation, field}`. Returns `RowError` per row. |
| `src/myntra/corrector.py` | Apply fixes + regenerate | `plan_corrections` (preview buckets: auto/drop/manual/unknown) and `correct(...)`: drops chosen SKUs, applies deterministic **auto-fixes** (pincode from constants; backfill empty ISP from MRP), applies **user answers vocab-validated** (`validate_value`; invalid → `summary["rejected"]`, never written; mirrors Prominent Colour into Brand Colour Remarks), then regenerates via `fill.fill_template`. |

**Honest-config note:** `error_rules.yaml` marks `image` and `stylegroupid` issues as
`explain_only` (their real fix is upstream S3 hosting / the ledger, not a cell edit) — the
corrector only auto-fixes what it deterministically can.

---

## 5. Layer 3 — Web app (FastAPI, "Marigold Ops")

Wraps layers 1–2 so non-technical staff can run them. FastAPI + Jinja + plain CSS + vendored
htmx. **No business logic here** — routers call `src/myntra` / `src/core`.

### The adoption mechanism

`POST /preview` used to be read-only — upload a filled sheet, get back listing cards, done. It now
**adopts** the uploaded workbook: it registers a new job in `src/web/jobs.py`'s `JobStore` and calls
`store.finish(job.id, {"filled": xlsx, "origin": "upload", ...})` directly, the same call a
completed *build* makes at the end of Flow A. `POST /preview/adopt-fix/{fix_id}` ends in the same
`_adopt()` helper, with a workbook it **rebuilds** for the photo-rejected SKUs (see below).

Because adoption produces a job that is indistinguishable in shape from a generated one, **every**
downstream surface — the Fill-attributes accordion, the vocabulary dropdowns, the live preview card,
per-panel save, the HSN gap banner, registry pinning, image replacement, download — operates on it
with **no special-casing**. The one place the two are told apart is `result["origin"]`: it's
literally `"upload"` for an adopted workbook (`preview.py` sets it explicitly), while a build never
writes an `origin` key at all — `generate.py`'s `store.finish(job_id, res)` passes through
`pipeline.main`'s result untouched, so every reader falls back to `job.result.get("origin",
"generate")`. Behaviourally the same value either way; only the adopted case is ever actually
stored. Templates use it only to decide UI
chrome that makes sense for one but not the other (the Clear button and the "edited" confirm guard
render only when `origin == "upload"` — a freshly-built job downloads from its own `_result.html`
panel instead). An unusable file is never adopted, and no job is ever created for one. The upload is written to a
staging directory under `RUNTIME` and read there first (`_rows_or_error`); only once it parses as a
Myntra sheet **and** yields at least one product row does the handler call `store.create()` and move
the file into the job dir. The two refusals — unreadable (`UNREADABLE`: a renamed CSV, another
workbook, last year's template, anything `read_filled_rows` raises on) and readable-but-rowless
(`NO_PRODUCTS`) — both render `_preview_error.html`, and a `finally` removes the staging directory
either way. This ordering matters: creating the job first meant a parse failure became an uncaught
500, which htmx does not swap on, so the screen showed nothing at all while the orphaned job and its
copy of the file survived for the life of the process. `POST /preview/adopt-fix` runs the same
`_rows_or_error` check on the workbook it rebuilds before adopting it.

**What `/preview/adopt-fix` adopts, and why it is not the corrected file.** The obvious source —
the fix run's `myntra_corrected.xlsx` — is wrong by construction. `corrector.py`'s
`correct_from_issues` `continue`s past every SKU with an `explain_only` issue, and an image
rejection *is* `explain_only`, so the corrected sheet excludes exactly the products the "Replace
images" button names. The route therefore reads `runtime/fix-<id>/issues.json`, selects the SKUs
whose issue is `action == "explain_only"` **and** `category == "image"`, and rebuilds a sheet for
just those through `regenerate_surface_b` — the same call `action=manual` uses, so their original
HSN and `styleGroupId` are pinned from the SKU registry. That rebuild re-runs the pipeline and so
needs `runtime/fix-<id>/products_export.csv`; when it is absent (the `sku_xlsx` path never demands
one) the route returns `fix._export_prompt_panel()` rather than rebuilding from nothing, and
`/fix/apply` now saves an attached export **before** its early returns so the owner is never asked
twice for the same file. A rejected SKU that the export does not contain comes back in
`could_not_rebuild` and is **named** in `_adopt_fix_partial.html`, which offers a plain link into
the SKUs that did rebuild — the sheet never silently omits a SKU the button promised.

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
| `src/web/routers/preview.py` | Flow C (below): **adopts** a workbook as a job — an upload, or one rebuilt for a fix run's photo-rejected SKUs — see "The adoption mechanism" below, then hands off to Flow D for editing. Reads `user_filled_attributes` from `rules.yaml`. |
| `src/web/routers/attributes.py` | Flow D (below): the in-app **Fill attributes** screen — drives both a freshly-built workbook and an adopted upload identically. `job_files(job_id)` locates the job's built workbook + Shopify export (404 `session expired, please re-upload`); `_panels(...)` joins sheet row ↔ SKU ↔ product photo; `_submitted(...)` / `_submitted_free(...)` / `_submitted_hsn(...)` parse the `attr__{ordinal}__{column_index}`, `free__{ordinal}__{column_index}`, `sku__{ordinal}` and `hsn__{ordinal}` form fields. Writes **only** the 12 attribute cells plus `tags`, `Brand Colour (Remarks)` and `HSN`. `_filled_count` / `_total` are the single shared definitions of "n of N", so the screen and the per-panel save cannot disagree. `attributes_save_images` (the `/images` route) reads up to 7 uploaded files per panel keyed `img__{ordinal}__{slot}` (`slot` 1-based over `IMAGE_COLUMNS`), calls `image_replace.prepare()` per slot so one bad photo fails only its own slot, then `image_replace.host()` once for everything that prepared cleanly, and writes the resulting URLs through `write_attributes` (never a bare openpyxl save). On any adopted (`origin == "upload"`) job, a successful attribute *or* image save sets `job.result["edited"] = True`, which is what makes the Clear button ask for confirmation. |
| `src/web/oauth.py` | Hosted-UI OAuth helpers (`authorize_url`/`exchange_code`/`logout_url`); stdlib urllib, injectable `http` so unit tests never hit the network. |
| `src/web/routers/auth_routes.py` | `GET /login` (state CSRF cookie → hosted UI), `GET /auth/callback` (verify state, exchange code, set `id_token` cookie), `GET /logout`. Sessions are **re-login-on-stale** (no refresh tokens). |

### Routes

| Method + path | Purpose |
|---|---|
| `GET /` | Home / landing. |
| `GET /generate` | Generate form; shows next styleGroupId from the ledger. |
| `POST /generate` | Upload CSV → duplicate-SKU guard → `reserve()` a batch → spawn background thread → return htmx stepper (header `x-job-id`). No HSN question: each product's code rides in on the export. |
| `POST /generate/new-only/{job_id}` | Duplicate guard: build only the NEW + EDITED SKUs. |
| `POST /generate/continue/{job_id}` | Duplicate guard override ("Continue anyway"): rebuild EVERY SKU in the file. SKUs the registry already knows (repeat + edited) are pinned back to their stored styleGroupId so a rework stays in the same Myntra style group; new SKUs draw from the ledger. HSN comes from the export as on any build. |
| `GET /generate/rebuild/{job_id}` | Duplicate guard: rebuild the REPEAT SKUs, pinning their stored styleGroupId + HSN (no ledger change). |
| `GET /jobs/{job_id}` | htmx poll: returns the stepper while running, `_cancelled.html` when stopped, the result partial when done/failed. Every one of those carries the `#run-controls` OOB fragment, so Stop appears and disappears with the run. |
| `POST /generate/cancel/{job_id}` | Stop a running build: sets the job's `cancel_requested` flag and returns the stepper immediately ("Stopping…"). The worker lands it at its next checkpoint — see the cancellation note below. |
| `GET /generate/download/{job_id}` | Download `myntra_filled.xlsx`. |
| `POST /generate/confirm/{job_id}` | `confirm()` the batch → **advances the ledger**. |
| `POST /generate/unconfirm/{job_id}` | Undo a mark-as-uploaded (refuses if a later batch was confirmed). |
| `POST /generate/style-start` + `/undo` | Seed the ledger from the last styleGroupId already used on Myntra. |
| `GET /generate/attributes/{job_id}` | Flow D form: one accordion panel per SKU — product photo, the 12 vocabulary-only dropdowns (pre-selected from the workbook), the free-text `tags` and **`HSN`** boxes, an `n/14 filled` counter, the read-only derived `Brand Colour (Remarks)`, and the current listing card. Above the panels, `_hsn_gap.html` counts the SKUs still missing a usable HSN — derived from the rows `_panels` already read, never a second `read_filled_rows`. |
| `POST /generate/attributes/{job_id}/preview` | htmx fragment: re-renders **one** listing card from the posted dropdown values via the same `build_card`. Touches no file. |
| `POST /generate/attributes/{job_id}` | Save (all panels): validate every value against the template vocabulary, then write all SKUs' attributes into the built workbook. Returns a 200 panel on success **and** on validation failure (htmx-swappable, never a 500); an off-vocab value writes nothing at all. |
| `POST /generate/attributes/{job_id}/one` | Save (one panel): the same validate-then-write path scoped to a single SKU, so a partial batch can be saved incrementally. |
| `POST /generate/attributes/{job_id}/images` | Replace one panel's images: up to 7 uploaded files → `image_replace.prepare()` per slot (per-slot failure isolation) → `image_replace.host()` once for what prepared → `write_attributes` with the new URLs. Sets `job.result["edited"]` on an adopted job. |
| `GET /preview` | Preview form (Flow C). |
| `POST /preview` | Upload a filled `.xlsx` → **adopts** it as a job (see "The adoption mechanism") and redirects (`HX-Redirect`) straight into `GET /generate/attributes/{job_id}` — i.e. Flow C now hands off to Flow D rather than rendering read-only cards itself. Rejects a file with no product rows. |
| `POST /preview/clear/{job_id}` | Discards an adopted job (`store.drop` + delete its runtime dir) and redirects to `GET /preview`. An unknown/already-cleared `job_id` is not an error — it lands on the same empty form. |
| `POST /preview/adopt-fix/{fix_id}` | Rebuilds a sheet for the fix run's photo-rejected SKUs (`issues.json` → `explain_only` + `category == "image"` → `regenerate_surface_b`), checks it with `_rows_or_error`, adopts it as a job and redirects into Flow D the same way `POST /preview` does — this is what the Fix screen's "Replace images" button calls. **Not** the corrected workbook, which excludes those SKUs by construction. 404s if the fix session is unknown or expired (no `issues.json`); answers with a panel — never a 500 — when there are no image rejections, no `products_export.csv`, or the rebuild raises. |
| `GET /fix` | Fix form. |
| `POST /fix` | Upload a rejection file (**3 formats:** per-SKU `.xlsx`, file-level `.csv`, or MDirect Listings Report) → detect format → classify → persist `rows.json` → return review partial (header `x-fix-id`) split into **correctable** vs **explain_only** groups. |
| `POST /fix/apply/{fix_id}` | Two submit actions from `_fix_review.html`: **`action=fix`** applies typed answers + drop checkboxes → `correct()` → corrected sheet of *only the correctable* SKUs ("Download now to fix"); **`action=manual`** rebuilds a fresh sheet for *only the explain_only* SKUs from an uploaded Shopify export, pinning their original HSN + styleGroupId ("Download listing file"). Surface-B correctable rebuilds and every manual rebuild need `products_export` (`needs_export`), which is saved to the fix dir **before** any early return so a later rebuild can reuse it; the whole handler is wrapped so any error returns a 200 error panel, never a swallowed 500. The result panel (`_fix_result.html`) also offers **"Replace images for N SKU(s)"** when any `manual_needed` issue has `category == "image"`, posting to `/preview/adopt-fix/{fix_id}`. |
| `GET /fix/download/{fix_id}` | Download the rebuilt `.xlsx`. |

### Flow A — Generate (request lifecycle)

```
POST /generate (CSV) ─► save to runtime/<job>/ ─► dedup guard
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

### Flow C — Preview & edit (request lifecycle)

Preview stopped being read-only: uploading a filled sheet now **adopts** it (see "The adoption
mechanism" above) and hands the browser straight into Flow D for editing. There is no separate
"preview card" screen reached from an upload any more — the card itself now lives *inside* Flow D
(`POST /generate/attributes/<job>/preview`), so an uploaded file and a freshly-built one see
exactly the same editing surface.

```
GET /generate/download/<job> ─► seller edits in Excel (optional) ─► saves
POST /preview (filled .xlsx) ─► stage under RUNTIME ─► _rows_or_error() ─► read_filled_rows()
                             ├► raises ─► _preview_error.html (UNREADABLE), no job ever created
                             ├► no rows ─► _preview_error.html (NO_PRODUCTS), no job ever created
                             └► rows ─► store.create() ─► move file in ─► store.finish(job,
                                        {filled: xlsx, origin: "upload", ...})
                                └► HX-Redirect → GET /generate/attributes/<job>   (Flow D, below)
                                (finally: the staging dir is removed on every path)

POST /preview/adopt-fix/<fix_id> ─► issues.json ─► SKUs with action=explain_only, category=image
                                  ├► none ─► _preview_error.html into #adopt-fix-out
                                  ├► no products_export.csv ─► _export_prompt_panel()
                                  └► regenerate_surface_b(image_skus) ─► _rows_or_error(rebuilt)
                                     ├► raises ─► _error_panel() (escaped), never a 500
                                     ├► could_not_rebuild ─► _adopt_fix_partial.html names them
                                     └► copy into a new job, same origin="upload"
                                        └► HX-Redirect → GET /generate/attributes/<job>
                                     (this is what the Fix screen's "Replace images" button calls)

POST /preview/clear/<job> ─► store.drop(job) + delete its runtime dir ─► HX-Redirect → GET /preview
seller ─► downloads the edited file from Flow D, then uploads THAT to Myntra
```

### Flow D — Fill attributes in-app (request lifecycle)

Additive: Flow C's Excel round-trip still works untouched. Filling here is **optional**. Reached
either from `_result.html`'s "✎ Fill attributes" button (a job this app just built) or, since Flow
C's adoption change, from an uploaded/adopted job — the two are indistinguishable to this screen.

```
GET /generate/attributes/<job>          (from _result.html, or redirected here by adoption)
    job_files() → runtime/<job>/{myntra_filled.xlsx, products_export.csv}
    read_filled_rows(xlsx) ─┬─ row ordinal ↔ vendorSkuCode ↔ Product.images[0]  (photo)
                            └─ attributes already in the sheet → pre-selected options
    attributes.html: one <details> panel per SKU (12 selects, options = vocab_by_header only,
                      + 7 image-slot file pickers, one per IMAGE_COLUMNS entry)

on every dropdown change ─► POST /generate/attributes/<job>/preview  (hx-include closest panel)
                         └► preview.build_card(posted values) ─► _preview_card.html fragment
                            (the SAME reconstruction /preview used to render directly — no JS
                            logic duplicate, and no separate read-only screen any more)

"Save attributes" / "Save this SKU" ─► POST /generate/attributes/<job>[/one]
    validate_submitted(values, vocab)   → off-vocab ⇒ 200 error panel, NOTHING written
    derive_brand_colour(values)         → Brand Colour (Remarks) = colour.lower()
    validate_hsn(raw)                   → blank clears; non-blank must be 8 digits, else raise
    write_attributes(xlsx, ...)         → row SKUs verified, cells written, blanks cleared
                                        → NUMERIC_HEADERS coerced (HSN as a number, not text)
                                        → fill.shared_to_inline() RE-APPLIED  (see below)
    sku_registry.update_hsn(...)        → INSIDE the write lock, only after a successful write,
                                          so a later fix-flow rebuild cannot restore a stale code
    sku_registry.update_names(...)      → same lock, same rule, for PINNED_NAME_HEADERS
                                          (List View Name, productDisplayName): a rebuild remaps
                                          those columns from the export, so an unpinned name is
                                          silently replaced by the Shopify title
    if origin == "upload": job.result["edited"] = True   → Clear now asks for confirmation
    _hsn_gap.html refreshed out of band  → top-level in the fragment, or htmx ignores it

"pick replacement photos" ─► POST /generate/attributes/<job>/images  (one panel, up to 7 files)
    for each non-empty slot: image_replace.prepare(sku, slot, bytes, specs, out_dir)
        → per-slot (local_path, key, None) on success, or (None, None, reason) on failure —
          NEVER raises, so one bad photo fails only its own slot
    image_replace.host(prepared, specs, out_dir)  → uploads what prepared, returns public URLs
        → raises ImageConfigError if hosting isn't configured (S3 bucket / base URL / s3_upload
          unset) — reported to the owner, nothing written, no local path ever lands in the sheet
    write_attributes(xlsx, ..., {ordinal, sku, values: {header: url, ...}})
        → same write-lock, same shared-string re-apply as an attribute save
    if origin == "upload": job.result["edited"] = True
GET /generate/download/<job> ─► the same file, now with the chosen attributes, replaced images,
                                 AND live dropdowns
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

`templates/`: `base.html` (shell), `home.html`, `generate.html`, `fix.html`, `preview.html`
(now just the upload form shell — `{% include "_preview_upload.html" %}`), `attributes.html`,
and htmx partials `_stepper.html`, `_result.html`, `_confirmed.html`, `_mark_upload.html`,
`_dedup_warn.html`, `_style_start.html`, `_hsn_gap.html`, `_cancelled.html`, `_run_controls.html`,
`_fix_review.html`, `_fix_result.html`, `_preview_upload.html` (the empty upload box, swapped back
in after Clear), `_preview_error.html` (the "no products found" refusal), `_preview_card.html`
(the one card markup, shared by Flows C and D), `_attr_panel.html`, `_attr_panel_saved.html`
(one-panel save response), `_attr_saved.html` (all-panels save response), `_attr_images.html`
(the 7 per-slot file pickers inside a panel), `_attr_images_saved.html` (image-save response:
per-slot success/failure), `_clear_button.html` (the Clear control, `hx-confirm` gated on
`edited`). `static/`: `app.css` (Marigold Ops theme: warm near-black bg, marigold `#E8A33D`
accent), vendored `htmx.min.js`, and vendored fonts (Space Grotesk / IBM Plex Mono / Inter) —
**no runtime CDN**.

`_preview.html` (the old read-only card-list template) is no longer referenced by any router —
Flow C's adoption change replaced it with a redirect into Flow D. Left in place rather than
deleted as part of a docs-only task; a future cleanup can remove it once confirmed dead.

`_run_controls.html` is the only **out-of-band** partial: every Generate-flow response
swaps it into the `#run-controls` slot beside the Generate button, filled with Stop while
a build runs and empty once it ends. Riding the stepper's existing 1-second poll makes it
self-healing — a missed swap corrects itself on the next tick. Note the button carries
`hx-params="none"`: it sits inside the upload form, and htmx would otherwise post the
form's values (including the CSV, under the inherited multipart encoding) on every click.

### Runtime working dirs — `src/web/runtime/`

Per-request scratch: `runtime/<job_id>/` (Generate: uploaded CSV + outputs) and
`runtime/fix-<id>/` (Fix: `rejection.xlsx`, `issues.json`, `products_export.csv`,
`myntra_corrected.xlsx`, and `replace-images/` for the adopt-fix rebuild). Git-ignored
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
| **S3 (images + ledger)** | `src/core/s3_upload.py`, `groupid_ledger.S3JsonStore`, `src/myntra/image_replace.py` (replacement leg) | Bucket `ijorethnicpartners`, region `ap-south-1`, image prefix `myntra/`, ledger key `state/myntra_groupid.json`. Images must be `.jpg` and public-read. Replacement images key as `{sku}/{slot}-{hash}.jpg` (content-addressed, not `{sku}/{n}.jpg`) — see §3. **Unverified:** the replacement upload leg has not yet been exercised against real AWS credentials, only against a fake/injected boto3 client in tests. |
| **Cognito (auth)** | `src/web/auth.py`, `settings.py`, `oauth.py`, `auth_routes.py` | Pool/client/domain + client secret (SecureString) in SSM; JWT validated by JWKS with `verify_at_hash: False` (Cognito id_tokens carry `at_hash`). Hosted-UI login round-trip (/login → /auth/callback → /logout) is **live**. |
| **ECR (image registry)** | `ci-cd.yml`, deploy systemd | Repo `marketplace-bulklisting`, `:latest` pulled on boot; CI `deploy` job restarts via SSM. |
| **SSM Parameter Store (config)** | `src/web/settings.py` | Per-field env→SSM fallback; prefix `/marketplace-listing/`; read on EC2 via instance role. Secret is a SecureString. No Secrets Manager. |

---

## 8. Configuration — `config/myntra/` (edit instead of code)

| File | Controls |
|---|---|
| `column_map.yaml` | Direct Shopify field → Myntra column copies. |
| `constants.yaml` | Fixed per-row values: brand, **manufacturer/packer/importer address with 6-digit pincode**, sizes, AgeGroup, FashionType, Year, Season, mandatory-attribute defaults. |
| `rules.yaml` | **`user_filled_attributes`** (the 12 seller-decided columns the mapper blanks — the single source of truth, read by `/preview` and the Fill-attributes screen too), fabric keyword detection (fed the retired HSN signature; no longer read by the build), `replicate_constant_across_numbered`, **`style_group_id_start`**, Product Details marker. |
| `image_specs.yaml` | Image min dims, max bytes, JPEG quality, max images; **S3 host** (`public_base_url`, `s3_upload`, `s3_bucket`, `s3_region`, `s3_prefix`). |
| `error_rules.yaml` | Maps Myntra error-message substrings → `{category, action, explanation, field}` for the Fix flow. |

---

## 9. Tests — `tests/` (222)

Layers 1–2 in `tests/*.py` (template reader, shopify reader, mapper, images, s3 upload, fill /
inline strings / dropdowns, report, models, config load, end-to-end, **groupid_ledger**,
**hsn_kb**, **sku_registry**, **error_reader**, **corrector**, **pipeline_override**,
**template_guard**, **preview**, **attribute_entry**, **image_replace**). Layer 3 in `tests/web/`
(settings, auth, jobs, pages, generate, fix, preview, **attributes**, and a real-pipeline fix e2e).
`python -m pytest -q` is the CI gate. (The test count in this heading predates the preview-edit /
image-replacement branch — new tests were added in `tests/test_image_replace.py` and across
`tests/web/test_preview.py` / `test_attributes.py` / `test_fix.py`, but the full suite has not been
re-run to get an updated total; see `docs/journal/2026-08-17.md`.)

Two of them guard invariants that fail *silently* if broken — do not delete them to make a
refactor pass: `test_write_attributes_keeps_strings_inline` (no `t="s"` cell survives an in-app
save) and `test_save_keeps_dropdowns_alive_in_the_downloaded_file` (the owner's Excel check).

Note: `tests/web/test_attributes.py` is **slow — ~12 min on its own** (measured 2026-08-17, down
from ~27 min once `read_filled_rows` stopped re-scanning a read-only sheet on every cell read;
see `docs/journal/2026-08-17.md`). What remains is genuine: nearly every test builds or re-saves
the V13 workbook through openpyxl. It is slow, not hung; do not kill it. While developing, run a
`-k` subset and budget properly for the whole file — and **profile one test before accepting any
of this as inherent**; that is how the 27→12 min bug was found. The full suite is longer still —
see `docs/journal/` for the standing guidance.

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
