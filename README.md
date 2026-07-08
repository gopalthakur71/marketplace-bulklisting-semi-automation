# Myntra Bulk-Listing Automation — Phase 1

Turn a **Shopify product CSV export** + the **Myntra DIY saree template** into a
**ready-to-upload, Myntra-accepted** sheet — with images hosted as Myntra-compliant
`.jpg` URLs — in one command, deterministically.

Built for **Ijor** (ethnic wear). Phase 1 scope: **sarees only**.

> **Status:** the generated file is **accepted by Myntra end-to-end** — a batch
> uploaded via this pipeline reached `CATALOGING_IN_PROGRESS` (SKUs created). Every
> upload error encountered has been diagnosed and fixed; see
> [Myntra upload requirements](#myntra-upload-requirements-hard-won) below.

> **Guiding principle:** the pipeline guesses nothing. All column mapping, pricing,
> and validation is plain code. Any value that does not match Myntra's allowed
> dropdown list is **flagged in a report, never silently written**. (LLM-based
> attribute enrichment is Phase 2.)

---

## Quick start

1. Place the inputs:
   - `input/products_export.csv` — Shopify product export
   - `templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx` — Myntra DIY saree template
     (the blank template / model file for upload)
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Set `style_group_id_start` in `config/myntra/rules.yaml` to **(your current Myntra catalog
   count) + 1** (see [styleGroupId](#3-styleGroupId-must-continue-from-your-catalog)).
4. (For image hosting) configure AWS credentials and the S3 settings in
   `config/myntra/image_specs.yaml` — see [Image hosting on S3](#image-hosting-on-s3).
5. Run:
   ```
   python run.py
   ```
6. Upload `output/myntra_filled.xlsx` to the Myntra DIY bulk uploader.

### Output (`output/`)
| File | Contents |
|---|---|
| `myntra_filled.xlsx` | The Myntra **Sarees** sheet filled with your products: numeric cells stored as numbers, image columns holding **public S3 `.jpg` URLs**, no stray rows, no dropdown validations (intentionally — see below) |
| `images/<sku>/<n>.jpg` | Each product's gallery converted to Myntra-compliant JPGs (one folder per SKU) |
| `report.txt` | Per-SKU log: fields filled, blanks left for manual fill, vocab flags, image pass/fail |

> ⚠️ Close `output/myntra_filled.xlsx` in Excel before re-running, or the script
> cannot overwrite it (PermissionError).

---

## What one run does, end to end

`python run.py` ([run.py](run.py)) wires the modules below together:

1. Reads the Myntra template (headers + dropdown vocab).
2. Reads + groups the Shopify export.
3. Maps each product → Myntra columns, applies pricing/constants/rules, validates vocab.
4. Downloads + converts each image to JPG.
5. **Uploads the JPGs to S3** (so they get public `.jpg` URLs).
6. Writes the Sarees sheet (numeric cells, S3 image URLs, cleared stray rows).
7. Writes `report.txt`.

---

## Major functions

### 1. Read the Myntra template + extract dropdown vocab — `src/myntra/template_reader.py`
- Detects the Sarees header row (`styleId` marker, row 3) and the first data row (row 4).
- The template's dropdowns are **37 Excel "x14" extension data-validations** that
  openpyxl cannot see. They are parsed straight from the raw sheet XML and resolved
  to their allowed-value lists in the `masterdata` sheet, producing an exact
  `{column → allowed values}` map. These lists are the controlled vocabularies that
  every written value is checked against.

### 2. Read + group the Shopify export — `src/core/shopify_reader.py`
- Loads the CSV and groups variant/image rows under each parent product (`Handle`).
- Forward-fills product-level fields (populated only on each product's first row).
- Collects the image gallery per product, ordered by `Image Position`.

### 3. Map columns + validate + apply business rules — `src/myntra/mapper.py`
- **Direct field mapping** (`config/myntra/column_map.yaml`): title, SKU, tags, description, fabric.
- **Deterministic pricing:** `MRP = Compare-At-Price (else Price)`, `ISP = Price`.
- **Constants on every row** (`config/myntra/constants.yaml`): brand / manufacturer /
  packer (full address **with 6-digit pincode**), size fields, AgeGroup, FashionType,
  Year, Season, etc.
- **Per-row rules** (`config/myntra/rules.yaml`):
  - HSN code applied when the product name contains a keyword (e.g. `cotton → 52081120`).
  - Prominent Colour derived by scanning the product name/description against the
    colour dropdown (longest/earliest match wins; small synonym map, e.g. golden→Gold).
- **Vocab validation:** every value targeting a dropdown column is matched
  (case-insensitive) to its allowed list and rewritten in Myntra's exact spelling.
  No match → blank cell + a flag in the report.

### 4. Convert images + emit hosted URLs — `src/core/images.py`
- Downloads each Shopify image (WebP/PNG/JPG) and outputs **JPG only**.
- **Flattens transparency onto white** before converting (`alpha_composite`), so
  transparent areas don't turn black.
- Validates minimum dimensions and file size; JPEG quality 90; saves one folder per
  SKU: `output/images/<sku>/1.jpg`, `…/2.jpg`, …
- Writes the **public S3 URL** (`public_base_url` + `/<sku>/<n>.jpg`) into the sheet for
  each passing image — **not** the Shopify URL, because Myntra rejects `.webp`
  (see below). Falls back to the source CDN URL only if `public_base_url` is unset.

### 5. Upload images to S3 — `src/core/s3_upload.py`
- Uploads this run's validated JPGs to `s3://<bucket>/<prefix>/<sku>/<n>.jpg` with
  `ContentType: image/jpeg`. The S3 key mirrors the local `output/images/` tree (via
  `base_dir`), so it matches the URL the sheet references. Only the files passed are
  uploaded (not a directory scan), so stale images from earlier batches aren't re-sent.
  Controlled by `s3_*` keys in `config/myntra/image_specs.yaml`; `main(upload=False)`
  disables it (used by tests).

### 6. Fill the sheet (Myntra-readable) — `src/myntra/fill.py`
- Writes mapped values into the Sarees sheet from row 4; first images →
  `Front/Side/Back/Detail/Look Shot/Additional 1–2` columns.
- **Stores numbers as numbers.** `NUMERIC_HEADERS` = {styleGroupId, HSN, MRP, ISP,
  Year, Net Quantity} are written as numeric cells (Myntra rejects text "1" as "non
  numeric").
- **Clears the whole data region first** so no stray template example rows reach
  Myntra (assigns `cell.value=None` *and* `cell.hyperlink=None` — both required).
- **Converts the Sarees sheet's shared strings to inline strings** post-save.
- **Does NOT re-inject dropdown validations** by default (`preserve_dropdowns=False`):
  the re-injected x14 XML breaks Myntra's Apache POI parser. A `preserve_dropdowns=True`
  copy is available only for manual editing, never for upload.

### 7. Report — `src/myntra/report.py`
- Emits `report.txt`: per-SKU filled-field count, blanks left for a manual pass,
  vocab flags (with the offending value), and image pass/fail — so there are **no
  silent gaps**.

---

## Myntra upload requirements (hard-won)

These are the rules a generated sheet must satisfy, learned from real upload errors.
Each is handled by the pipeline:

| Requirement | Why / error seen | Where handled |
|---|---|---|
| **No stray/example rows** | The blank template ships with example image URLs in row 11 (no brand) → read as an extra product, `SHEET_VALIDATION_FAILED` / null-brand | `fill.py` clears the data region |
| **No re-injected dropdowns** | Hand-injected x14 validations break Myntra's POI parser | `fill.py` `preserve_dropdowns=False` |
| **`styleGroupId` continues from your catalog** | "Style SKU Count … minimum unique StyleGroupIds" — ids must not start at 1 if you already have listings | `style_group_id_start` in `rules.yaml` |
| **Manufacturer/Packer carry a 6-digit pincode** | "6 digit Pincode is missing in manufacturer/packer name and address" | `constants.yaml` (full address) |
| **MRP/ISP present and numeric** | "MRP … non numeric" / "ISP cannot be empty for DIY source" | pricing in `mapper.py` + numeric cells in `fill.py` |
| **Image URLs end in `.jpg`/`.jpeg`** | "extension is not jpg/jpeg" — Myntra checks the URL string literally; Shopify URLs end in `.webp` | S3 hosting (`images.py` + `s3_upload.py`) |

> **Note on strings:** Myntra reads both shared-string and inline-string files fine
> *once stray rows are cleared* — the earlier "shared strings unreadable" theory was a
> misdiagnosis. The pipeline emits inline strings for the Sarees sheet either way.

---

## Image hosting on S3

Myntra ingests images by URL and requires the URL to **end in `.jpg`/`.jpeg`**.
Shopify CDN URLs end in `.webp` (served as JPEG via content negotiation, but the
string is `.webp`), so they are rejected. The pipeline therefore hosts the converted
JPGs on S3 and writes those `.jpg` URLs.

- **Bucket / region:** `ijorethnicpartners` in `ap-south-1`, prefix `myntra/`.
- **URL pattern:** `https://ijorethnicpartners.s3.ap-south-1.amazonaws.com/myntra/<sku>/<n>.jpg`
  (one folder per SKU; the S3 key mirrors the local `output/images/<sku>/<n>.jpg` layout)
- **Public read:** the `myntra/*` prefix is public via a bucket policy
  (`S3/s3-bucket-policy-ijor-public-read.json`); the rest of the bucket stays private.
- **IAM:** the uploading user is scoped with object put/get + list on the bucket
  (`S3/iam-policy-s3-image-upload.json`, scoped to the `myntra/` prefix); it has no
  `s3:PutObjectAcl`, no `s3:DeleteObject`, and no
  bucket-policy admin.
- **Cost:** negligible (~$0.001/mo storage; egress within AWS's free tier). Myntra
  copies images to its own CDN during cataloging, so S3 is hit only briefly — but
  keep the images until cataloging completes.

Config (`config/myntra/image_specs.yaml`):
```yaml
public_base_url: "https://ijorethnicpartners.s3.ap-south-1.amazonaws.com/myntra"
s3_upload: true
s3_bucket: ijorethnicpartners
s3_region: ap-south-1
s3_prefix: myntra
```
Set `s3_upload: false` (or leave `public_base_url` empty) to skip S3 and fall back to
the source CDN URL — useful for offline/dry runs.

---

### styleGroupId must continue from your catalog
Myntra requires unique `styleGroupId`s that don't collide with products you've already
listed. Set `style_group_id_start` in `config/myntra/rules.yaml` to **(current catalog count)
+ 1** before each batch; `run.py` assigns `styleGroupId = start + row_index`.

---

## Configuration (no code changes needed)

| File | Purpose |
|---|---|
| `config/myntra/column_map.yaml` | Shopify field → Myntra Sarees column (direct copies) |
| `config/myntra/constants.yaml` | Fixed values written to every row (incl. brand + manufacturer/packer address with pincode) |
| `config/myntra/rules.yaml` | Per-row rules: HSN-by-name-keyword, Prominent-Colour-from-name, colour synonyms, `style_group_id_start` |
| `config/myntra/image_specs.yaml` | Image min dimensions, max file size, JPEG quality, max images; S3 host + upload settings |

If Myntra changes a column or vocabulary, it's a one-line config edit.

---

## What Phase 1 deliberately does NOT do
- **No attribute invention.** Fields Shopify doesn't carry (Occasion, Pattern, Border,
  measurements, …) use review-defaults or are left blank and listed in the report.
  (Phase 2 fills these via LLM.)
- **No persistent dedup / ledger.** Only an in-run check. (Phase 2 adds the SQLite
  ledger + create/update/skip routing.)
- **No Myntra verification.** Upload status is checked manually in the Myntra panel.
  (Phase 2 reconciles against a Myntra catalog export.)

---

## Tech stack
Python 3.12 · pandas · openpyxl · Pillow · PyYAML · requests · boto3 · pytest.

## Tests
```
python -m pytest -v
```
171 tests cover x14 vocab parsing, variant grouping, vocab validation, pricing,
HSN/colour rules, transparency flatten, dropdown handling, numeric cell storage,
S3 upload (stubbed, incl. per-SKU key mirroring), an end-to-end run, the
styleGroupId ledger, HSN knowledge base, per-SKU dedup guard, error-file
classification + correction, and the full web app (Generate + Fix flows).

---

## CI/CD

On every push to `main`, GitHub Actions runs the test suite and, if it passes,
builds a Docker image and pushes it to a private Amazon ECR repo
(`marketplace-bulklisting`, `ap-south-1`). Authentication uses GitHub OIDC —
**no AWS keys are stored in GitHub**. Pull requests run the test job only.

- Workflow: `.github/workflows/ci-cd.yml`
- One-time AWS setup: `docs/runbooks/cicd-aws-setup.md`
- Design: `docs/superpowers/specs/2026-06-25-listing-app-cicd-deploy.md`

Deferred to a later phase: running the image on a start-on-demand EC2 t3.micro
(spec §7) and the FastAPI web server it serves.

---

## Project docs
- Design spec: `docs/superpowers/specs/2026-06-24-myntra-phase1-deterministic-fill-design.md`
- Implementation plan: `docs/superpowers/plans/2026-06-24-myntra-phase1-deterministic-fill.md`
- Day journals: `docs/journal/2026-06-24.md`, `docs/journal/2026-06-25.md`
