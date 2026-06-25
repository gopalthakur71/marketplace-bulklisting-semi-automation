# App Architecture & File Map

Myntra Bulk-Listing Automation — Phase 1. This document maps **every file to what it
does** and shows how data flows through the app. For usage and the Myntra upload
rules, see [README.md](../README.md).

---

## Data flow (one `python run.py`)

```
 Shopify CSV ─┐
              ├─► shopify_reader ─► [Product]                      (group variants + image gallery)
 Myntra xlsx ─┴─► template_reader ─► TemplateInfo                 (headers + 37 dropdown vocabularies)
                                          │
                                          ▼
                    mapper.map_product(Product, TemplateInfo, config) ─► MappedRow
                       • constants (brand, addresses w/ pincode, sizes …)
                       • pricing  (MRP = compareAt|price, ISP = price)
                       • rules    (HSN by fabric, Prominent Colour, synonyms)
                       • vocab validation (flag, never guess)
                                          │
 each Product ─► images.process_images ─► ImageResult              (download → JPG → S3 .jpg URL)
                                          │
                       s3_upload.upload_images(output/images/*) ─► s3://…/myntra/
                                          │
                    fill.fill_template(rows) ─► output/myntra_filled.xlsx
                       • numeric cells, S3 image URLs
                       • clear stray rows, shared→inline strings, no dropdowns
                                          │
                    report.write_report(rows) ─► output/report.txt
```

`run.py` orchestrates all of the above and is the **single entry point**.

---

## Layout — marketplace split

Code is split so a second marketplace (e.g. Amazon) is added as a sibling of
`myntra/` without touching the shared core:

```
run.py                     # thin entry point -> src.myntra.pipeline.cli()
src/
  core/                    # marketplace-agnostic (reused by every marketplace)
    models.py  shopify_reader.py  images.py  s3_upload.py
  myntra/                  # Myntra-specific
    template_reader.py  mapper.py  fill.py  report.py  pipeline.py
config/
  myntra/                  # Myntra config (column_map, constants, rules, image_specs)
templates/
  myntra/                  # Myntra blank template + reference upload file
input/                     # Shopify export (products_export.csv)
errors/
  myntra/                  # captured Myntra upload errors (git-ignored)
```

To add a marketplace: create `src/<name>/` (its own template reader / mapper /
sheet writer / `pipeline.py`) + `config/<name>/` + `templates/<name>/`, reusing
`src/core/`.

## Source code — `src/core/` (shared)

| File | Responsibility | Key details |
|---|---|---|
| `src/core/models.py` | Dataclasses shared across modules | `Product`, `Flag`, `MappedRow`, `ImageResult`, `TemplateInfo`. |
| `src/core/shopify_reader.py` | Read + group Shopify export | Groups variant/image rows by `Handle`, forward-fills product fields, orders image gallery by `Image Position` → `[Product]`. |
| `src/core/images.py` | Image conversion | Downloads each image, flattens transparency onto white, writes JPG to `<out>/<sku>/<n>.jpg`, validates size/dimensions; emits the **public S3 `.jpg` URL** (`public_base_url`) for the sheet. |
| `src/core/s3_upload.py` | Host images | Uploads given JPG paths to `s3://<bucket>/<prefix>/<sku>/<n>.jpg` (key mirrors the local tree via `base_dir`) as `image/jpeg`. Client injectable for tests. |

## Source code — `src/myntra/` (Myntra-specific)

| File | Responsibility | Key details |
|---|---|---|
| `src/myntra/pipeline.py` | Orchestrator (`main`/`cli`) | Loads `config/myntra/`, runs the pipeline per product, assigns `styleGroupId` (offset by `style_group_id_start`), gates S3 use, writes outputs. `main(upload=False)` skips S3 (tests). |
| `src/myntra/template_reader.py` | Read Myntra template | Detects header row (`styleId`), first data row; parses the **37 x14 extension dropdowns** from raw sheet XML → `{column → allowed values}` (`masterdata`). |
| `src/myntra/mapper.py` | Map + validate + business rules | Constants, pricing, HSN-by-fabric, Prominent Colour scan + synonyms, free-text Brand Colour (Remarks), vocab validation (flags non-matches). Returns `MappedRow`. |
| `src/myntra/fill.py` | Write the Sarees sheet | Numeric cells (`NUMERIC_HEADERS`), S3 image URLs, **clears stray template rows**, shared→inline strings, dropdowns NOT re-injected by default (`preserve_dropdowns=False`). |
| `src/myntra/report.py` | Audit report | `output/report.txt`: per-SKU filled count, blanks, vocab flags, image pass/fail. |
| `run.py` *(root)* | Entry point | Thin wrapper → `src.myntra.pipeline.cli()`. |

---

## Configuration — `config/myntra/` (edit instead of code)

| File | Controls |
|---|---|
| `config/myntra/column_map.yaml` | Direct Shopify field → Myntra column copies (title, sku, tags). |
| `config/myntra/constants.yaml` | Fixed values on every row: brand, **manufacturer/packer/importer address with 6-digit pincode**, sizes, AgeGroup, FashionType, Year, Season, mandatory-attribute defaults. |
| `config/myntra/rules.yaml` | Fabric detection (→ Saree/Blouse Fabric, Wash Care, HSN), Prominent Colour scan, colour synonyms (`golden→Gold`, `ivory→White`), `brand_colour_remarks_from_prominent`, `style_group_id_start`, Product Details marker. |
| `config/myntra/image_specs.yaml` | Image min dimensions, max bytes, JPEG quality, max images; **S3 host** (`public_base_url`, `s3_upload`, `s3_bucket`, `s3_region`, `s3_prefix`). |

---

## AWS / S3 — `S3/`

Images must be served from `.jpg` URLs (Myntra rejects Shopify's `.webp`). These JSON
policies set up the S3 hosting; apply them in the AWS console (see README → Image
hosting on S3).

| File | Purpose |
|---|---|
| `S3/iam-policy-s3-image-upload.json` | Minimal least-privilege policy for the uploading IAM user: `PutObject`/`GetObject` scoped to `ijorethnicpartners/myntra/*` + list (no `PutObjectAcl`, no delete, no account-id ARNs). |
| `S3/s3-bucket-policy-ijor-public-read.json` | Bucket policy granting anonymous `s3:GetObject` on `ijorethnicpartners/myntra/*` only (rest of bucket stays private). |

---

## Inputs & templates

`pipeline._resolve()` prefers a subdir and falls back to the repo root.

| File | Role |
|---|---|
| `input/products_export.csv` | Shopify product export (source data). |
| `templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx` | Myntra DIY saree template / model file for upload (headers + dropdown vocab). |
| `templates/myntra/Myntra-Upload-Pending.xlsx` | Reference known-good upload (recipe for mandatory attribute values). |

Per-marketplace templates live under `templates/<marketplace>/`.

## Outputs — `output/` (generated, git-ignored)

| File | Role |
|---|---|
| `output/myntra_filled.xlsx` | The upload sheet for all products in the CSV. |
| `output/images/<sku>/<n>.jpg` | Converted JPGs, one folder per SKU (mirrored to S3). |
| `output/report.txt` | Per-SKU audit. |
| `output/Myntra-*-2026-*.xlsx` | Hand-curated per-batch deliverables built from the proven working file. |

## Error captures — `errors/<marketplace>/` (git-ignored)

Marketplace upload errors live under `errors/<marketplace>/` — e.g. `errors/myntra/`
holds Myntra resubmission files (`*.xlsx` with `STATUS` / `SYSTEM ERROR MESSAGE`
columns) and `*.csv` error exports, kept for diagnosis. Not part of the app.

---

## Tests — `tests/` (28 tests)

| File | Covers |
|---|---|
| `test_template_reader.py` | Header detection, x14 dropdown vocab parsing. |
| `test_shopify_reader.py` | Variant grouping, image gallery ordering. |
| `test_mapper.py` | Pricing, HSN/colour rules, vocab validation/flagging. |
| `test_images.py` | JPG conversion, transparency flatten, `public_base_url` → S3 URLs. |
| `test_s3_upload.py` | Upload keys + content-type (stubbed S3 client). |
| `test_fill.py` / `test_inline_strings.py` / `test_dropdowns.py` | Sheet write, inline strings, no-dropdowns-by-default. |
| `test_report.py`, `test_models.py`, `test_config_loads.py`, `test_end_to_end.py` | Report, dataclasses, config load, full pipeline (numeric cells, S3 URL). |
| `conftest.py` | Shared fixtures. |

---

## Docs — `docs/`

| File | Role |
|---|---|
| `docs/ARCHITECTURE.md` | This file — file map + data flow. |
| `docs/journal/2026-06-24.md`, `…-06-25.md` | Day journals (decisions + the full upload-error chronology). |
| `docs/superpowers/specs/…design.md` | Phase 1 design spec. |
| `docs/superpowers/plans/…fill.md` | Implementation plan. |
| `Myntra_Listing_Automation_Report.docx` *(root)* | Original requirements brief. |
