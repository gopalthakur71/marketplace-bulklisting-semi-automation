# Myntra Bulk-Listing Automation

Turn a **Shopify product CSV export** + the **Myntra DIY saree template** into a
**ready-to-upload, Myntra-accepted** sheet — with images hosted as Myntra-compliant
`.jpg` URLs — deterministically, and let a non-technical teammate do it from a browser.

Built for **Ijor** (ethnic wear). Current listing scope: **sarees**.

The project ships in two layers, both live:

- **The pipeline** (`run.py`, `src/core` + `src/myntra`) — the deterministic CLI that
  reads Shopify, maps to Myntra, validates vocabulary, converts + hosts images, and
  writes the upload sheet.
- **The web app** (`src/web`, "Marigold Ops") — a FastAPI + htmx UI wrapping the
  pipeline so non-technical teammates can **Generate** upload sheets and **Fix** rows
  Myntra rejected, without touching code, Python, or AWS. Deployed on EC2 behind
  Cognito login, shipped by a GitHub Actions → ECR CI/CD pipeline.

> **Status:** generated files are **accepted by Myntra end-to-end** — a batch uploaded
> via this pipeline reached `CATALOGING_IN_PROGRESS` (SKUs created). Every upload error
> encountered has been diagnosed and fixed (see
> [Myntra upload requirements](#myntra-upload-requirements-hard-won)). The web app is
> **deployed to production** on EC2 with Cognito auth and Gemini-backed error
> explanation enabled. **190 tests pass.**

> **Guiding principle:** the pipeline **guesses nothing.** All column mapping, pricing,
> and validation is plain code. Any value that doesn't match Myntra's allowed dropdown
> list is **flagged in a report, never silently written.** HSN codes are **learned from
> the user once and reused**, never invented. The **12 attributes Myntra builds the public
> title and description from are left blank with live dropdowns for the seller to choose**
> — the machine never picks them. The one LLM in the system (Gemini) only **explains**
> rejection errors in plain English — it never fixes, guesses, or supplies a value.

---

## Table of contents

- [Quick start](#quick-start)
- [The web app (Marigold Ops)](#the-web-app-marigold-ops)
  - [Flow A — Generate](#flow-a--generate)
  - [Flow B — Fix Myntra errors](#flow-b--fix-myntra-errors)
  - [Flow C — Preview the Myntra listing](#flow-c--preview-the-myntra-listing)
- [The pipeline — what one run does](#the-pipeline--what-one-run-does-end-to-end)
- [Major pipeline modules](#major-pipeline-modules)
- [Myntra upload requirements (hard-won)](#myntra-upload-requirements-hard-won)
- [Image hosting on S3](#image-hosting-on-s3)
- [Persistent state (JSON stores)](#persistent-state-json-stores)
- [Auth, config & secrets](#auth-config--secrets)
- [Configuration files](#configuration-files)
- [Tech stack](#tech-stack)
- [Tests](#tests)
- [CI/CD & deployment](#cicd--deployment)
- [Project docs](#project-docs)

---

## Quick start

### Inputs
- `input/products_export.csv` — Shopify product export
- `templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx` — the blank Myntra DIY saree
  template the pipeline uses (headers + dropdown vocabulary). Its data-validations are
  **plain**, so openpyxl carries the dropdowns into the generated file; the older
  `Myntra-Sku-Template-2026-06-16.xlsx` is kept only for the x14-parsing tests.

### Install
```
pip install -r requirements.txt
```

### Option 1 — CLI (one command)
1. Set `style_group_id_start` in `config/myntra/rules.yaml` to **(your current Myntra
   catalog count) + 1** (see [styleGroupId](#stylegroupid-must-continue-from-your-catalog)).
2. (For image hosting) configure AWS credentials and the S3 settings in
   `config/myntra/image_specs.yaml` — see [Image hosting on S3](#image-hosting-on-s3).
3. Run:
   ```
   python run.py
   ```
4. Upload `output/myntra_filled.xlsx` to the Myntra DIY bulk uploader.

> ⚠️ Close `output/myntra_filled.xlsx` in Excel before re-running, or the script can't
> overwrite it (PermissionError).

#### CLI output (`output/`)
| File | Contents |
|---|---|
| `myntra_filled.xlsx` | The Myntra **Sarees** sheet filled with your products: numeric cells stored as numbers, image columns holding **public S3 `.jpg` URLs**, no stray rows, no dropdown validations (intentionally — see below) |
| `images/<sku>/<n>.jpg` | Each product's gallery converted to Myntra-compliant JPGs (one folder per SKU) |
| `report.txt` | Per-SKU log: fields filled, blanks left for manual fill, vocab flags, image pass/fail |

### Option 2 — Web app (local)
```
AUTH_DISABLED=1 \
LEDGER_LOCAL_PATH=.state/ledger.json \
HSN_LOCAL_PATH=.state/hsn_kb.json \
SKU_REGISTRY_LOCAL_PATH=.state/sku_registry.json \
uvicorn src.web.main:app --reload
```
Open `http://localhost:8000`. `AUTH_DISABLED=1` injects a fake dev user so no AWS/Cognito
is needed; supply a full `.env` and the config loader makes **zero AWS calls**. The
three `*_LOCAL_PATH` vars point the JSON state stores at local files. Set
`GEMINI_API_KEY` + `EXPLAIN_WITH_GEMINI=1` to enable plain-English error explanation
(optional — the app degrades gracefully to the hand-authored dictionary and raw text).

---

## The web app (Marigold Ops)

A server-rendered FastAPI + Jinja + **htmx** app (no React, no Node, no build step, no
runtime CDN). The visual direction is "Marigold Ops": warm near-black background, marigold
accent, Space Grotesk / IBM Plex Mono / Inter, all CSS and `htmx.min.js` vendored into
`src/web/static/`. Jobs run in-process (`BackgroundTasks` + an in-memory job store); htmx
polls a status endpoint (~1 s) and lights up a live pipeline stepper. No Celery, no Redis,
no database — the only durable state is a handful of JSON stores (in S3 in prod, local
files in dev; see [Persistent state](#persistent-state-json-stores)).

Every route gates on a valid Cognito session (or the `AUTH_DISABLED` dev user). Two flows
share the nav.

### Flow A — Generate

Upload a Shopify CSV → get a Myntra-ready sheet. The flow has guardrails that pause it:

1. **Upload** (`POST /generate`) — drop `products_export.csv`; marketplace = Myntra. The
   styleGroupId start is read from the **ledger** and shown (not typed).

2. **Duplicate-generation guard** — before building, the app maps each SKU and computes a
   `content_hash` (excluding `styleGroupId` + `HSN`), then partitions the batch against
   the **SKU registry** into **NEW / REPEAT / EDITED**. If **any** SKU was already
   generated, it stops and warns rather than silently rebuilding + burning fresh
   styleGroupIds. You can then:
   - **⬇ Download the already-generated sheet** — a deterministic **rebuild-on-demand**
     that re-runs the pipeline with each SKU's **pinned styleGroupId and HSN** from the
     registry, producing a byte-for-byte-identical file (no new S3 object stored).
   - **Generate the N new SKUs only** — proceed with just the NEW/EDITED SKUs.

3. **HSN knowledge-base review** (`awaiting_hsn` state) — HSN is mandatory in the Myntra
   sheet but absent from the Shopify export, and depends on finer attributes than the
   data carries, so it **can't be computed** — but it **can be learned once and reused.**
   The app pre-scans the batch's unique **signatures** (`category | fabric`, e.g.
   `saree|pure silk`), looks each up in the **HSN KB**, and shows one review row per
   signature with the product names behind it, a required 8-digit HSN input, and
   **suggestion chips** (previously-used codes for that signature, each showing the past
   product names that used it). Known signatures are still shown, never silently
   auto-filled — **the user always gives the go.** On submit each code is validated
   (exactly 8 digits), `learn()`ed into the KB, and injected into the mapper.

4. **Progress** — the background task runs `pipeline.main()`; htmx polls `GET /jobs/{id}`
   and re-renders the stepper (Ingest → Map → Images→S3 → Fill & validate → Ready) with
   live counts.

5. **Result** — download `myntra_filled.xlsx`; inline report (rows written, images
   uploaded, vocab flags, blanks); the **assigned styleGroupId range**; a bold
   **"verify the file yourself before uploading"** reminder; and ledger controls:
   - **Mark upload successful** → `confirm(batch_id)` advances the ledger counter.
     *Reserve does not advance; only confirm does* — a failed/abandoned upload frees its
     IDs for reuse.
   - **Undo** the confirm (`unconfirm`) — guarded so only the most-recently confirmed
     batch can be rolled back (won't reissue IDs a later batch already consumed).

6. **Manual styleGroupId seed** — if you listed products **outside the app**, the ledger
   counter is behind reality. An **[Edit]** control on the Generate form lets you enter
   the **last used styleGroupId**; the app records `next = value + 1` (with an audit-trail
   **[Undo]**), snapping the counter to the truth so the next batch won't collide.

7. **Fill the seller-decided attributes** — the generated sheet leaves **12 columns blank
   on purpose**, each carrying its live Myntra dropdown: Prominent / Second / Third
   Prominent Colour, Saree Fabric, Blouse Fabric, Type, Ornamentation, Border, Pattern,
   Print or Pattern Type, Wash Care, Usage. Myntra **builds the public title and the
   "Design Details" text from these attributes**, so a guessed value publishes a wrong
   title — the machine never picks them. Open the file in Excel, choose from the
   dropdowns, save, and continue to Flow C.

### Flow B — Fix Myntra errors

Upload a rejection file → get a plain-English explanation of every error and, where the
fix is instant-text, a regenerated corrected sheet. **Human-gated throughout** (Proceed /
Do not change).

1. **Accepts all three real Myntra formats** (detected by column *presence*, not sheet
   name or order):

   | Type | Ext | Fingerprint | Carries full product data? |
   |---|---|---|---|
   | Per-SKU rejection (`SKU_VALIDATION_FAILED`) | `.xlsx` | `STATUS` + `SYSTEM ERROR MESSAGE` + product columns | **Yes** — correct in place (Surface A) |
   | File-level rejection (`SHEET_VALIDATION_FAILED`) | `.csv` | `ROW NO`, `STATUS`, `SYSTEM ERROR MESSAGE` | No — rebuild from records/export (Surface A′) |
   | Listings Report (`MDirect`) | `.csv` | `style status`, `seller sku code`, `onhold reason` | No — rebuild from records/export (Surface B) |

2. **Explanation engine** — cheapest-first, first hit wins, per error clause:
   1. **Hand-authored YAML rule** (`error_rules.yaml`, substring match) → explanation **+
      the only source of an auto-fix action**.
   2. **Self-learning store** (match on a normalized error *signature* — digit runs, SKU
      codes, URLs stripped to placeholders so the same error for different SKUs collapses
      to one entry) → cached explanation.
   3. **Gemini** (explain-only, Gemini Flash) → explanation → **written into the learned
      store** keyed by signature → served. Called **at most once per error type, ever.**
      It receives **only the error text**, never the product row — manufacturer/packer
      name, address, and pincode never leave the machine.
   4. **Fallback** — retry with backoff → show the raw message.

   The Listings Report's `onhold reason` is already plain English and passes through
   untouched (no Gemini).

   > **Non-negotiable invariants:** the LLM explains, never fixes or guesses. Auto-fixes
   > come only from human-authored YAML rules. Code flags, human decides. The app only
   > auto-fixes what a user can supply instantly as text (brand, pincode, address, colour,
   > price); image/quality/cropping problems are explained, never touched.

3. **Two groups, two actions** — the review screen splits rejected SKUs:
   - **"We can fix these"** (correctable / instant-text) → **Fix & download now** rebuilds
     just those SKUs with the fix applied. `auto_fix` values come from `constants.yaml`;
     `manual_choice` values are typed into a free-text box and validated against Myntra's
     real vocabulary before writing (invalid values are reported as `rejected`, never
     silently written). A read-only "closest Myntra names" hint may be shown — **no option
     buttons.**
   - **"You must fix these yourself first"** (explain-only — photos, quality, resolution)
     → guidance for the reshoot → Shopify → re-export workflow, plus **Download listing
     file for these SKUs**: after you re-shoot in Shopify and re-export those SKUs, the app
     rebuilds a Myntra sheet for **only** them, pinning the **same HSN and styleGroupId as
     the first attempt** (from the SKU registry) so Myntra won't re-reject on identity
     drift. This lives on the Fix page precisely because Generate would trip the
     duplicate-SKU guard.

4. **Data-source resolver** — for Surface A the data is in the uploaded file. For Surfaces
   A′/B the app looks each rejected SKU up in the **SKU registry**, falling back to the
   **Shopify export**, to rebuild the rows; a SKU resolvable in neither is reported
   "couldn't rebuild — data not found," never silently dropped.

5. **Correction-log breadcrumb** — every corrected SKU appends one record (timestamp, SKU,
   signature, before/after changes) to an append-only log. Nothing reads it yet; it
   accumulates so a future phase can learn which fixes actually worked.

### Flow C — Preview the Myntra listing

Upload the **filled** sheet back (`POST /preview`) and see, per SKU, what the listing will
look like **before** it goes to Myntra. Read-only — the app never modifies the file you
upload; you send that same file to Myntra.

Each card has two zones with deliberately different reliability:

- **Specifications — exact.** The attribute values you entered, as Myntra will show them.
- **Title & Design Details — approximate, and badged as such.** Myntra *generates* these
  from the attributes (it ignores the product name and description we submit), so the app
  reconstructs them from rules reverse-engineered off live Ijor listings:
  title ≈ `[Print/Pattern] [Ornamentation] [Saree Fabric] [Type] "Saree" [+ "With
  Unstitched Blouse Piece"]` — **colour is not in a saree title** — and Design Details as
  `"{Colour} {Type} sarees"` / `"{Pattern} saree with {Border} Border"` / `"Has
  {Ornamentation} detail"`. We do not try to match Myntra word-for-word, and we say so on
  the screen rather than pretending to a precision we don't have.

Any of the 12 attributes still blank is flagged on the card, so a missed dropdown is
caught here rather than by Myntra.

---

## The pipeline — what one run does, end to end

`python run.py` ([run.py](run.py)) — and the web app's Generate job — wire these modules
together:

1. Read the Myntra template (headers + dropdown vocab).
2. Read + group the Shopify export.
3. Map each product → Myntra columns; apply pricing/constants/rules; validate vocab;
   set HSN from the KB-injected `signature → hsn` map.
4. Download + convert each image to JPG.
5. **Upload the JPGs to S3** (for public `.jpg` URLs).
6. Write the Sarees sheet (numeric cells, S3 image URLs, cleared stray rows).
7. Write `report.txt`.

`pipeline.main()` accepts overrides used by the web layer: `style_group_id_start` (from
the ledger), `hsn_by_signature` (from the HSN KB review), and per-SKU
`style_group_id_by_sku` / `hsn_by_sku` (from the registry, for deterministic rebuilds).

---

## Major pipeline modules

### 1. Read the Myntra template + extract dropdown vocab — `src/myntra/template_reader.py`
- Detects the Sarees header row (`styleId` marker, row 3) and the first data row (row 4).
- Resolves each dropdown to its allowed-value list on the `masterdata` sheet, producing an
  exact `{column → allowed values}` map — the controlled vocabularies every written value is
  checked against, and the only source of options offered to the seller.
- Two validation dialects: the current template stores **plain `dataValidation` entries**
  (openpyxl reads *and* preserves them, which is why the generated file keeps live
  dropdowns); the older 2026-06-16 template stored **37 "x14" extension validations** that
  openpyxl silently drops, so those are parsed straight from the raw sheet XML. Plain is
  tried first, x14 is the fallback.
- `src/myntra/template_guard.py` then asserts the active template actually has every header
  the config and pipeline write. A template swap fails **loudly** instead of quietly
  dropping a column.

### 2. Read + group the Shopify export — `src/core/shopify_reader.py`
- Loads the CSV, groups variant/image rows under each parent product (`Handle`).
- Forward-fills product-level fields (populated only on each product's first row).
- Collects the image gallery per product, ordered by `Image Position`.

### 3. Map columns + validate + apply business rules — `src/myntra/mapper.py`
- **Direct field mapping** (`column_map.yaml`): title, SKU, tags, description, fabric.
- **Deterministic pricing:** `MRP = Compare-At-Price (else Price)`, `ISP = Price`.
- **Constants on every row** (`constants.yaml`): brand / manufacturer / packer (full
  address **with 6-digit pincode**), size fields, AgeGroup, FashionType, Year, Season, etc.
  Numbered constant columns (e.g. `Country Of Origin2…5`) are auto-replicated from the base
  value via `replicate_constant_across_numbered` in `rules.yaml`.
- **The 12 seller-decided attributes are popped, not filled** (`user_filled_attributes` in
  `rules.yaml`). Myntra generates the public title and description from them, so they are
  a human judgment: the mapper leaves the cells blank with their dropdowns intact. No
  colour scanning, no fabric guessing, no synonym map, no pre-fill.
- **HSN** set from the injected `signature → hsn` map (from the HSN KB); an unresolved
  signature is **flagged**, never guessed.
- **Vocab validation:** every value targeting a dropdown column is matched
  (case-insensitive) to its allowed list and rewritten in Myntra's exact spelling. No
  match → blank cell + a report flag.

### 4. Convert images + emit hosted URLs — `src/core/images.py`
- Downloads each Shopify image (WebP/PNG/JPG) and outputs **JPG only**.
- **Flattens transparency onto white** before converting (`alpha_composite`), so
  transparent areas don't turn black.
- Validates minimum dimensions and file size; JPEG quality 90; one folder per SKU
  (`output/images/<sku>/1.jpg`, …).
- Writes the **public S3 URL** into the sheet for each passing image — not the Shopify URL,
  because Myntra rejects `.webp`. Falls back to the source CDN URL only if `public_base_url`
  is unset.

### 5. Upload images to S3 — `src/core/s3_upload.py`
- Uploads this run's validated JPGs to `s3://<bucket>/<prefix>/<sku>/<n>.jpg` with
  `ContentType: image/jpeg`; the S3 key mirrors the local `output/images/` tree, so it
  matches the URL the sheet references. Only the passed files are uploaded (not a directory
  scan), so stale images from earlier batches aren't re-sent. `main(upload=False)` disables
  it (tests).

### 6. Fill the sheet (Myntra-readable) — `src/myntra/fill.py`
- Writes mapped values into the Sarees sheet from row 4; first images →
  `Front/Side/Back/Detail/Look Shot/Additional 1–2` columns.
- **Stores numbers as numbers.** `NUMERIC_HEADERS` = {styleGroupId, HSN, MRP, ISP, Year,
  Net Quantity} written as numeric cells (Myntra rejects text "1" as "non numeric").
- **Clears the whole data region first** so no stray template example rows reach Myntra
  (sets `cell.value=None` *and* `cell.hyperlink=None` — both required).
- **Converts the Sarees sheet's shared strings to inline strings** post-save — Myntra's
  parser doesn't resolve shared strings, and openpyxl re-creates them on *every* save, so
  any code that re-saves a built workbook must re-apply this step.
- **Does NOT re-inject x14 dropdown validations** by default (`preserve_dropdowns=False`):
  the re-injected x14 XML breaks Myntra's Apache POI parser. It isn't needed either — the
  current template's plain validations survive the save on their own, so the downloaded
  file has working dropdowns *and* uploads cleanly.

### 6b. Reconstruct the Myntra listing — `src/myntra/preview.py`
- `read_filled_rows()` reads a filled workbook back by header (no hard-coded row numbers).
- `reconstruct_title()` / `reconstruct_design_details()` rebuild what Myntra will generate
  from the attributes; `missing_attributes()` flags any of the 12 still blank. Treats a
  literal `NA` as "not set". Powers Flow C — read-only, and honest about being approximate.

### 7. Report — `src/myntra/report.py`
- Emits `report.txt`: per-SKU filled-field count, blanks left for a manual pass, vocab
  flags (with the offending value), image pass/fail — so there are **no silent gaps.**

### Orchestration & web-facing modules
| Module | Role |
|---|---|
| `src/myntra/pipeline.py` | `main()` — wires the above; accepts ledger/HSN/registry overrides; returns the assigned range + report for the UI |
| `src/myntra/groupid_ledger.py` | styleGroupId ledger: `reserve` / `confirm` / `unconfirm` / `set_next` |
| `src/myntra/hsn_kb.py` | HSN knowledge base: `signature` / `read_kb` / `suggest` / `learn` |
| `src/myntra/sku_registry.py` | Per-SKU pin (content hash + styleGroupId + HSN): `content_hash` / `partition` / `record` |
| `src/myntra/error_sources.py` | Fix flow: detect + read all 3 error formats → normalized `ErrorItem` list |
| `src/myntra/signature.py` | Error clause → normalized signature (+ captured values) |
| `src/myntra/explainer.py` | Orchestrates YAML → learned store → Gemini → raw |
| `src/myntra/explanation_store.py` | Self-learning explanation dictionary (atomic JSON) |
| `src/myntra/gemini_client.py` | Explain-only Gemini call + guardrails + fallback |
| `src/myntra/corrector.py` | Bucket routing, Surface-B data resolution, regenerate corrected sheet |
| `src/myntra/correction_log.py` | Append-only breadcrumb of what was fixed |
| `src/web/main.py` · `settings.py` · `auth.py` · `oauth.py` · `jobs.py` · `routers/*` | The FastAPI app (see [The web app](#the-web-app-marigold-ops)) |

---

## Myntra upload requirements (hard-won)

Rules a generated sheet must satisfy, learned from real upload errors — each handled by
the pipeline:

| Requirement | Why / error seen | Where handled |
|---|---|---|
| **No stray/example rows** | The blank template ships example image URLs in row 11 (no brand) → read as an extra product, `SHEET_VALIDATION_FAILED` / null-brand | `fill.py` clears the data region |
| **No re-injected dropdowns** | Hand-injected x14 validations break Myntra's POI parser | `fill.py` `preserve_dropdowns=False` |
| **`styleGroupId` continues from your catalog** | "Style SKU Count … minimum unique StyleGroupIds" — ids must not start at 1 if you already have listings | ledger / `style_group_id_start` |
| **Manufacturer/Packer carry a 6-digit pincode** | "6 digit Pincode is missing in manufacturer/packer name and address" | `constants.yaml` (full address) |
| **MRP/ISP present and numeric** | "MRP … non numeric" / "ISP cannot be empty for DIY source" | pricing in `mapper.py` + numeric cells in `fill.py` |
| **Image URLs end in `.jpg`/`.jpeg`** | "extension is not jpg/jpeg" — Myntra checks the URL string literally; Shopify URLs end in `.webp` | S3 hosting (`images.py` + `s3_upload.py`) |
| **HSN present (8-digit)** | HSN mandatory but absent from Shopify; wrong/missing HSN rejected | HSN KB review + `hsn_by_signature`; per-SKU pin keeps re-lists consistent |
| **Numbered constant columns filled** | `Country Of Origin2…5` are vocab-controlled and blank | `replicate_constant_across_numbered` in `mapper.py` |

> **Note on strings:** Myntra reads both shared-string and inline-string files fine *once
> stray rows are cleared* — the earlier "shared strings unreadable" theory was a
> misdiagnosis. The pipeline emits inline strings for the Sarees sheet either way.

---

## Image hosting on S3

Myntra ingests images by URL and requires the URL to **end in `.jpg`/`.jpeg`.** Shopify
CDN URLs end in `.webp` (served as JPEG via content negotiation, but the string is
`.webp`), so they're rejected. The pipeline hosts the converted JPGs on S3 and writes
those `.jpg` URLs.

- **Bucket / region:** `ijorethnicpartners` in `ap-south-1`, prefix `myntra/`.
- **URL pattern:** `https://ijorethnicpartners.s3.ap-south-1.amazonaws.com/myntra/<sku>/<n>.jpg`
  (one folder per SKU; the S3 key mirrors the local `output/images/<sku>/<n>.jpg` layout).
- **Public read:** the `myntra/*` prefix is public via a bucket policy
  (`S3/s3-bucket-policy-ijor-public-read.json`); the rest of the bucket stays private.
- **IAM:** the uploading identity has object put/get + list scoped to the `myntra/` prefix
  (`S3/iam-policy-s3-image-upload.json`); no `PutObjectAcl`, no `DeleteObject`, no
  bucket-policy admin.
- **Cost:** negligible (~$0.001/mo storage; egress within AWS's free tier). Myntra copies
  images to its own CDN during cataloging, so S3 is hit only briefly — but keep the images
  until cataloging completes.

Config (`config/myntra/image_specs.yaml`):
```yaml
public_base_url: "https://ijorethnicpartners.s3.ap-south-1.amazonaws.com/myntra"
s3_upload: true
s3_bucket: ijorethnicpartners
s3_region: ap-south-1
s3_prefix: myntra
```
Set `s3_upload: false` (or leave `public_base_url` empty) to skip S3 and fall back to the
source CDN URL — useful for offline/dry runs.

### styleGroupId must continue from your catalog
Myntra requires unique `styleGroupId`s that don't collide with products you've already
listed. In the web app the **ledger** manages this (with a manual-seed override). For the
CLI, set `style_group_id_start` in `rules.yaml` to **(current catalog count) + 1** before
each batch; `run.py` assigns `styleGroupId = start + row_index`.

---

## Persistent state (JSON stores)

No database. Five append-mostly JSON stores, each following the same pattern — an S3
object in prod (via `S3JsonStore`), a local file in dev (env-configurable path, since the
local store is one-file-per-path), atomic writes:

| Store | Prod key | Env (dev) | Holds |
|---|---|---|---|
| **styleGroupId ledger** | `state/myntra_groupid.json` | `LEDGER_LOCAL_PATH` | `next_style_group_id` + batches (reserve/confirm/undo/set_next) |
| **HSN knowledge base** | `state/hsn_kb.json` | `HSN_LOCAL_PATH` | `category\|fabric` signature → HSN code(s) + example names + counts |
| **SKU registry** | `state/sku_registry.json` | `SKU_REGISTRY_LOCAL_PATH` | Per-SKU pinned `content_hash` + styleGroupId + HSN (duplicate guard + deterministic rebuild) |
| **Learned explanation store** | alongside the registry | `EXPLANATION_STORE_PATH` | Error signature → learned plain-English template (written when Gemini explains something new) |
| **Correction log** | alongside the registry | `CORRECTION_LOG_PATH` | Append-only breadcrumb of every fix applied (for a future learning phase) |

The ledger's confirm-not-reserve model, single-batch workflow, and "replace JSON with a DB
only if this goes multi-tenant/SaaS" are deliberate — see the design specs.

---

## Auth, config & secrets

- **Cognito hosted-UI login.** `src/web/oauth.py` + `routers/auth_routes.py` implement the
  authorization-code round-trip (`/login` → Cognito → `/auth/callback` → `/logout`) with a
  CSRF `state` cookie; `auth.py` verifies the returned `id_token` JWT (RS256, issuer,
  audience, expiry) on **every** request. Session model is **re-login on stale** (no
  refresh tokens; id-token validity bumped to ~8h in the Cognito console). Add/remove
  teammates in the Cognito console — no code change.
- **`AUTH_DISABLED=1`** injects a fixed dev user and skips all Cognito work — identical
  code path, only the env differs, so the whole app runs locally with no AWS reachable.
- **Layered config loader** (`settings.py`): each value resolves **env var first, else
  SSM Parameter Store** (or Secrets Manager for the Cognito client secret). AWS is
  contacted only for values not supplied via env. Credentials come from the default chain
  locally, the **EC2 instance role** in prod (no static keys in the container).
- **Cookies:** `id_token` is `HttpOnly; SameSite=Lax`; `Secure` is off until TLS is added
  (a `COOKIE_SECURE` flag flips it on with no code change). Access is IP-restricted in the
  meantime.

---

## Configuration files

| File | Purpose |
|---|---|
| `config/myntra/column_map.yaml` | Shopify field → Myntra Sarees column (direct copies) |
| `config/myntra/constants.yaml` | Fixed values written to every row (brand + manufacturer/packer address with pincode, sizes, etc.) |
| `config/myntra/rules.yaml` | `user_filled_attributes` (the 12 seller-decided columns left blank), fabric keywords for the HSN signature, numbered-constant replication, `style_group_id_start` (CLI) |
| `config/myntra/image_specs.yaml` | Image min dimensions, max file size, JPEG quality, max images; S3 host + upload settings |
| `config/myntra/error_rules.yaml` | Fix flow: error-message substring → plain-English explanation **+ action** (`auto_fix` / `manual_choice` / `explain_only`); the **only** source of auto-fixes |

If Myntra changes a column or vocabulary, it's a one-line config edit.

---

## Tech stack

Python 3.12 · **FastAPI** · **uvicorn** · **Jinja2** · **htmx** (vendored) ·
python-multipart · python-jose (JWT) · pandas · openpyxl · Pillow · PyYAML · requests ·
boto3 · pytest. AWS: **S3**, **SSM Parameter Store**, **Secrets Manager**, **Cognito**,
**ECR**, **EC2** (ap-south-1). LLM: **Google Gemini Flash** (explain-only, optional).
No Node, no Tailwind, no Celery/Redis, no database.

---

## Tests

```
python -m pytest -v
```
**190 tests** cover vocab parsing (both plain and x14 validations), the template-compatibility
guard, variant grouping, vocab validation, pricing, the blanked seller-decided attributes, the
listing-preview reconstruction, transparency flatten, dropdown handling, numeric cell storage,
inline strings, S3 upload (stubbed, incl. per-SKU key mirroring), an end-to-end run, the
styleGroupId ledger (reserve/confirm/undo/set_next), the HSN knowledge base, the per-SKU
dedup guard + pipeline overrides, error-file classification across all 3 formats, error
signature normalization, the self-learning explanation store, the Gemini client
(mocked — asserts no product data leaks), the corrector, and the full web app (Generate,
HSN review, dedup, Fix two-action flow incl. a real non-mocked manual-rebuild e2e, the
listing Preview, auth, oauth, settings loader, jobs store, pages).

---

## CI/CD & deployment

On every push to `main`, GitHub Actions runs the test suite and, if it passes, builds a
Docker image and pushes it to a private Amazon ECR repo (`marketplace-bulklisting`,
`ap-south-1`). Authentication uses **GitHub OIDC — no AWS keys are stored in GitHub.**
Pull requests run the test job only.

Deployment target is a **start/stop EC2 t3.micro**: a systemd unit pulls `:latest` from
ECR on boot and runs the container, so *starting the box = deploying the newest build*.
The app is **live in production** on EC2 behind Cognito login, IP-restricted (no TLS /
`0.0.0.0/0` yet — a later chunk), with Gemini enabled (key in SSM, `EXPLAIN_WITH_GEMINI=1`
in the systemd unit). All AWS access is via the EC2 instance role.

- Workflow: `.github/workflows/ci-cd.yml`
- One-time AWS setup: `docs/runbooks/cicd-aws-setup.md`, `cicd-aws-setup-console.md`
- Deploy / Cognito / SSM+Secrets / Gemini runbooks: `docs/runbooks/web-ec2-deploy-console.md`,
  `web-cognito-setup-console.md`, `web-ssm-secrets-setup-console.md`,
  `add-gemini-api-key-ssm.md`, `enable-gemini-ec2-systemd.md`
- Infra & cost maps: `docs/infra-resources.md`, `docs/infra-costs.md`

---

## Project docs

- **Plain-English guides:** `docs/APP-FEATURES-GUIDE.md` (every feature),
  `docs/TECH-EXPLAINED-FOR-BEGINNERS.md` (every technology, zero prior knowledge)
- **Architecture map:** `docs/ARCHITECTURE.md` (+ `AGENTS.md` at the repo root)
- **Design specs:** `docs/superpowers/specs/` — Phase 1 deterministic fill, cloud/CI-CD
  deploy, Phase 2 FastAPI web, Cognito auth, HSN KB + app fixes, SKU dedup guard,
  fix-error flow, fix manual-rebuild
- **Implementation plans:** `docs/superpowers/plans/`
- **Day journals:** `docs/journal/`
- **Decisions:** `docs/decisions/`
