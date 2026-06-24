# Phase 1 — Deterministic Template Fill (Design)

**Project:** Myntra Bulk-Listing Automation — Shopify → Myntra template fill pipeline
**Owner:** Gopal Thakur (Ijor, ethnic wear; Phase 1 scope = sarees only)
**Date:** 2026-06-24
**Status:** Design approved, ready for implementation plan

## 1. Goal

Turn a Shopify product CSV export plus the Myntra DIY saree template into a
ready-to-upload Myntra sheet with Myntra-compliant JPG images, in one command —
deterministically. No LLM, no API, no database in Phase 1.

**Guiding principle (from the project report):** the model decides nothing here.
Pricing, dedup, mapping, and validation are all code. Phase 1 contains no model
at all.

## 2. Real Input Files (verified 2026-06-24)

These are the actual files in the project root, already inspected:

### `products_export.csv` (Shopify export)
- 7 unique products (`Handle`), 59 rows. One variant each (sarees are free-size,
  `Option1 Value = "Default Title"`).
- The extra rows per product carry the **image gallery**: each row has an
  `Image Src` (a `.webp` Shopify CDN URL) and `Image Position` (1..N).
- Product-level fields (`Title`, `Vendor`, `Tags`, `Variant Price`,
  `Variant Compare At Price`, `Variant SKU`, and the metafield columns
  `Color (...color-pattern)`, `Fabric (...fabric)`, `Size (...size)`) are
  populated **only on each product's first row** and must be forward-filled
  within the group.

### `Myntra-Sku-Template-2026-06-16.xlsx` (Myntra DIY template)
- Sheets: `__INSTRUCTIONS`, **`Sarees`** (fill target), **`masterdata`** (vocab).
- `Sarees`: column **headers are on row 3** (row 1 = version, row 2 = section
  banners, row 4 = first data row per the validation ranges). 80 columns.
  - Identity/business: `styleId`, `vendorSkuCode`, `vendorArticleNumber`,
    `vendorArticleName`, `brand`, `articleType`, `SKUCode`, `MRP`, `ISP`,
    `Country Of Origin`, `HSN`, etc.
  - Attributes: `Prominent Colour`, `Usage`, `Type`, `Saree Fabric`,
    `Blouse Fabric`, `Blouse`, `Pattern`, `Print or Pattern Type`,
    `Ornamentation`, `Border`, `Occasion`, `Wash Care`, ...
  - **Image columns 74–80** (7 slots): `Front Image`, `Side Image`,
    `Back Image`, `Detail Angle`, `Look Shot Image`, `Additional Image 1`,
    `Additional Image 2`.
- **Dropdowns:** the template has **37 Excel x14 (extension) data-validations**
  in the `Sarees` sheet, each mapping a column range to a vocab range in
  `masterdata` (e.g. `F4:F303 → masterdata!$B$2:$B$53080` = `brand`;
  `masterdata` col 22 `Colour` → Prominent Colour; col 40 `AttributesValueId2187`
  Art Silk/Pure Silk/... → `Saree Fabric`; col 54 `AttributesValueId2194`
  Daily/Party/Festive/... → `Occasion`; etc.).
  - **openpyxl silently drops these** ("Data Validation extension is not
    supported and will be removed"). They must therefore be read from the raw
    sheet XML, not via openpyxl's validation API.

## 3. Folder Contract

```
project-root/
  run.py                       # one-command entry: `python run.py`
  src/
    template_reader.py         # Sarees headers + x14 validation -> vocab map
    shopify_reader.py          # CSV -> per-product records + image gallery
    mapper.py                  # column_map.yaml apply + vocab validation
    images.py                  # download/load, flatten, WebP->JPG, validate, name
    report.py                  # collect + emit report.txt
    fill.py                    # write mapped rows into the Sarees sheet
  config/
    column_map.yaml            # Shopify column -> Myntra Sarees column
    image_specs.yaml           # min dims, aspect ratio, max size, JPEG quality
    vocab/                     # extracted allowed-value lists (generated)
  input/
    products_export.csv        # (currently in project root; run reads from here or input/)
    Myntra-Sku-Template-2026-06-16.xlsx
  output/
    myntra_filled.xlsx         # generated, ready to upload
    images/                    # converted JPGs (SKU_1.jpg, SKU_2.jpg, ...)
    report.txt                 # what filled, what was flagged
  tests/                       # TDD suite against the real files as fixtures
```

Run = "drop files in `input/`, run `python run.py`". Input paths are configurable
so the current root-level files work without moving them.

## 4. Modules (each one job, testable in isolation)

| Module | Responsibility | Key interface |
|---|---|---|
| `template_reader` | Read Sarees headers (row 3); parse raw `sheet2.xml` x14 validations to build `{sarees_column -> [allowed values]}`; persist to `config/vocab/`. | `read_template(path) -> TemplateInfo{headers, vocab_by_column, first_data_row}` |
| `shopify_reader` | Load CSV, group rows by `Handle`, forward-fill product fields, collect ordered image gallery. | `read_products(path) -> list[Product]` |
| `mapper` | Apply `column_map.yaml`; for each mapped value, validate against vocab; return filled cells + flags. Never write an invalid/guessed value. | `map_product(product, template_info, column_map) -> (cells, flags)` |
| `images` | For each product: download/load each gallery image, flatten transparency onto white (`alpha_composite` then `convert('RGB')`), encode JPEG q90, validate dims/aspect/size, name `SKU_n.jpg`, return per-image pass/fail. | `process_images(product, specs, out_dir) -> ImageResult` |
| `fill` | Write mapped cells + image references into the Sarees sheet from `first_data_row`, save `output/myntra_filled.xlsx`. | `fill_template(template_path, rows, out_path)` |
| `report` | Aggregate: rows filled, fields left blank, vocab flags, image pass/fail; write `output/report.txt`. | `write_report(results, path)` |

`run.py` wires these together and is the only orchestrator.

## 5. Processing Flow

1. `template_reader` reads headers + builds the vocab map from x14 validations.
2. `shopify_reader` produces one record per product, with its ordered image list.
3. For each product, `mapper` fills directly-mappable Myntra columns from the
   Shopify record and validates each against vocab.
4. `images` converts that product's gallery to compliant JPGs (`SKU_1.jpg`...),
   mapping the first 7 into image columns 74–80.
5. `fill` writes all rows into the Sarees sheet and saves `myntra_filled.xlsx`.
6. `report` emits `report.txt`.

## 6. Column Mapping (initial `column_map.yaml`)

Direct, explicit mappings only — examples (final list finalized in implementation):

- `Title` → `vendorArticleName` / `productDisplayName`
- `Variant SKU` → `vendorSkuCode` / `SKUCode`
- `Variant Price` → `MRP` (and `ISP` per pricing rule, deterministic)
- `Color (...color-pattern)` → `Prominent Colour` (validated vs Colour vocab)
- `Fabric (...fabric)` → `Saree Fabric` (validated vs fabric vocab)
- `Vendor` / brand constant → `brand`
- Country of origin / HSN: constants from config where Shopify lacks them.

Fields with no Shopify source are **left blank** (see §7).

## 7. Deliberately NOT in Phase 1

- **No attribute invention.** Myntra-only fields absent from Shopify
  (`Pattern`, `Print or Pattern Type`, `Occasion`, `Blouse`, `Blouse Fabric`,
  `Ornamentation`, `Border`, `Wash Care`, measurements) are **left blank and
  listed in `report.txt`** for a manual pass. Phase 2 fills these via LLM.
- **No persistent dedup.** In-run duplicate check only (within the single CSV).
- **No Myntra verification.** Uploads confirmed manually.

## 8. Image Conversion Rules

- Input: WebP/PNG/JPG. Output: **JPG only** (Myntra requirement).
- **Transparency trap:** flatten onto white via `Image.alpha_composite` *before*
  `convert('RGB')`, or transparent areas render black.
- Validate min dimensions, aspect ratio, file size against `image_specs.yaml`;
  **flag failures**, don't upload a bad image.
- JPEG **quality 90** default.
- Naming: `SKU_1.jpg`, `SKU_2.jpg`, ... per product, in `output/images/`.

## 9. Open / Deferred Decisions

- **Dropdown preservation on write (DEFERRED by owner):** openpyxl drops the 37
  x14 validations when saving. Phase 1 focuses first on **filling the sheet with
  correct values**. How to preserve dropdowns in the output (direct zip XML edit
  vs. re-inject extension XML) will be decided *after* filling works correctly.
  Until then, the output file may lose dropdown validations — acceptable for
  initial fill verification.

## 10. Deliverables & Acceptance

**Deliverables**
- Working `run.py` + config-driven `column_map.yaml`, `image_specs.yaml`, and
  generated `config/vocab/` lists.
- One-command run producing `myntra_filled.xlsx` + `output/images/`.
- `report.txt` flagging blanks and validation issues.
- README / DEPLOY notes for repeat runs.

**Acceptance criteria**
- The real Shopify export produces a Myntra sheet whose **filled** fields carry
  no format/vocab errors (every written attribute is a valid vocab value).
- All processed images emerge as valid JPGs, correctly named, transparency
  handled; failures are flagged not uploaded.
- Every unfilled or flagged field appears in `report.txt` — no silent gaps.

## 11. Technical Stack

Python 3.12 · pandas (CSV) · openpyxl (xlsx read/write) · Pillow (images) ·
PyYAML (config) · raw XML parsing (stdlib) for x14 validation extraction.
Build + test via TDD with the real files as fixtures. Git initialized locally.

## 12. Build Method

Test-Driven Development. The error-prone parts get tests first: transparency
flatten correctness, vocab-mismatch flagging (never silently write), variant
grouping + forward-fill, x14 validation parsing, image naming/slotting.
