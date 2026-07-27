# Spec: In-App Attribute Entry (per-SKU accordion + live listing preview)

**Project:** Myntra bulk-listing pipeline (Shopify CSV → Myntra template, sarees only)
**Type:** New web surface in the Generate flow (additive; no existing path changes behaviour)
**Design approved:** 2026-07-26 · **Spec written:** 2026-07-27
**Branch:** `feat/attribute-mapping-vocab`
**Builds on:** `docs/superpowers/specs/2026-07-24-myntra-attribute-excel-roundtrip-preview-design.md`
(that spec's Feature A — blank attribute columns with Excel dropdowns — and Feature B — the `/preview`
round-trip — both stay exactly as built)

---

## 1. Problem

The 2026-07-24 build made the name-driving attributes **user-decided**: the pipeline writes those columns
blank with live Myntra dropdowns, the owner fills them in Excel, and re-uploads the file to `/preview` to
see the reconstructed listing. The owner ran that round-trip for real and confirmed the preview output is
right.

The remaining problem is **drudgery, not correctness**: every batch requires download → open Excel →
scroll a 200-column sheet → find the right columns → fill each row → save → re-upload to preview → repeat
if something looks wrong. The product photo (the thing you actually judge "is this Zari or Embroidered?"
against) is not in Excel at all, so the owner works from memory or a second window.

## 2. Goals / Non-goals

**Goals**

1. Fill the user-decided attributes **inside the app**, one SKU at a time, with the **product photo** and a
   **live listing preview** on screen while choosing.
2. Dropdown options come **strictly from the Myntra template vocabulary** — no invented values.
3. The downloaded file still carries **working Excel dropdowns**, so the Excel path remains fully usable.

**Non-goals**

- ❌ No change to the existing upload-filled-xls → `/preview` flow. It stays, untouched.
- ❌ No auto-fill, no guessing, no synonym learning (unchanged from the 2026-07-24 decision).
- ❌ No injected `NA` option. `NA` is offered only where the template vocabulary actually contains it.
- ❌ No mandatory step — the owner may fill all, some, or none in-app.
- ❌ No change to brand, HSN, pricing, images, styleGroupId, or Product Details logic.

## 3. Scope — the 12 user-filled columns

The existing 9 plus 3 new ones (`Second Prominent Colour`, `Third Prominent Colour`, `Usage`). All 12 are
verified present in the V13 template (`templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx`) with plain
list validations:

| Column | Vocab size | Contains `NA`? |
|---|---:|---|
| Prominent Colour | 53 | yes |
| Second Prominent Colour | 53 | yes |
| Third Prominent Colour | 53 | yes |
| Saree Fabric | 42 | no |
| Blouse Fabric | 36 | yes |
| Type | 41 | yes |
| Ornamentation | 17 | yes |
| Border | 10 | no |
| Pattern | 9 | no |
| Print or Pattern Type | 22 | no |
| Wash Care | 3 | no |
| Usage | 9 | yes |

The three new columns are added to `user_filled_attributes` in `config/myntra/rules.yaml`. That single list
stays the source of truth: the mapper leaves those columns blank, `/preview` reads it to know which columns
are user-owned, and the new screen reads it to know which dropdowns to render. Adding the three columns
therefore also widens the `/preview` specs table and its missing-attribute check — intended.

Vocabulary is read live from the template each run via the existing `read_template()`
(`vocab_by_header`). Nothing is hard-coded and nothing is appended to a vocab list.

## 4. Flow and placement

Unchanged up to the built file:

1. `/generate` → upload Shopify CSV → dedup guard → HSN review → build. The pipeline writes
   `myntra_filled.xlsx` into the job directory with the 12 columns **blank + dropdowns**, exactly as today.
2. The result panel (`_result.html`) gains a primary action **"Fill attributes"** alongside the existing
   **"Download xlsx"**. Download remains available without visiting the new screen.
3. **"Fill attributes"** opens the new screen (§5). The owner fills what they want and presses **Save**.
4. Save writes the chosen values into the already-built `myntra_filled.xlsx` (§6) and returns to the result
   panel with a confirmation and the same Download button. Columns left on the placeholder stay blank and
   keep their Excel dropdown.
5. The owner downloads and uploads to Myntra as before. `/preview` still works on the downloaded file for a
   final check, and still works for files filled purely in Excel.

The screen is **optional at every level** — skip it entirely, or fill 3 of 12 fields on 2 of 9 SKUs.

## 5. The "Fill attributes" screen

### 5.1 Layout

One **accordion panel per SKU**, collapsed by default, header showing `vendorSkuCode` + product title + a
"n/12 filled" counter (computed from what is saved in the file at page load; it is not live-updated as
dropdowns change — the live preview is the feedback while editing). Expanding a panel reveals three zones side by side (stacking on narrow screens):

- **Photo** — the product's first Shopify image URL, matched by SKU. No image → a neutral placeholder tile.
- **12 dropdowns** — one `<select>` per column, options = that column's template vocabulary in template
  order, preceded by a UI-only `— choose —` placeholder whose value is the empty string.
- **Live listing preview** — the same card `/preview` renders (approximate title, Design Details, exact
  specifications), re-rendered on every dropdown change.

One **Save** button at the bottom of the page submits **all SKUs at once**.

### 5.2 Live preview — server-side (Option A, confirmed)

Every `<select>` change fires an htmx request for that panel only
(`hx-post="/generate/attributes/{job_id}/preview"`, `hx-trigger="change"`, `hx-include` scoped to the
panel, `hx-target` the panel's preview div). The handler builds an attribute dict from the posted values and
calls the **existing** `reconstruct_title` / `reconstruct_design_details` / `missing_attributes` in
`src/myntra/preview.py`, returning the shared card partial.

This is deliberate: no title/description logic is duplicated in JavaScript, so the live preview and
`/preview` can never disagree. Cost is one small request per dropdown change — acceptable for a
single-user internal app.

### 5.3 Shared-rendering refactor (part of this work)

To make "can never disagree" structural rather than aspirational:

- Extract the card-building dict from `src/web/routers/preview.py` into
  `src/myntra/preview.py:build_card(attrs, user_filled)`. Both surfaces call it.
- Extract the card markup from `_preview.html` into `_preview_card.html`. `_preview.html` loops over cards
  and includes the partial; the live-preview endpoint renders the partial directly.
- Move the `user_filled_attributes` YAML read out of the preview router into the new shared module (§7) so
  there is one loader, not two.

No other refactoring.

## 6. Save — writing into the built workbook

`write_attributes(xlsx_path, template, values_by_row)`:

1. `openpyxl.load_workbook(xlsx_path)` — **not** `read_only`, **not** `data_only`.
2. For each row ordinal `i`, target sheet row `template.first_data_row + i`. **Verify** the row's
   `vendorSkuCode` equals the SKU the form claims for that ordinal; a mismatch raises rather than writing to
   the wrong product.
3. Write each of the 12 cells: a chosen value is written as-is; the empty placeholder writes `None`
   (blanking the cell). This makes save **idempotent** — re-saving with different choices overwrites, and
   clearing a field back to `— choose —` genuinely clears it.
4. `wb.save(xlsx_path)` in place.
5. **Re-run the shared-string→inline conversion** on the `Sarees` sheet (§6.2).

### 6.1 Dropdowns must survive (key invariant)

V13's dropdowns are plain `<dataValidation type="list">` entries, which openpyxl preserves through
load→save (this is exactly why V13 was adopted). The save path therefore keeps them — but this is the
feature's single most fragile assumption, so it gets an explicit round-trip test (§8).

### 6.2 Shared strings must stay inline (found while writing this spec)

`src/myntra/fill.py` does not just `wb.save()` — it then runs `_shared_to_inline()` over the `Sarees` sheet
XML, because **Myntra's upload parser does not resolve shared strings**. A plain openpyxl re-save
re-introduces shared strings and would silently undo that, producing a file Myntra rejects.

So the save step must re-apply the same conversion after saving. `_shared_to_inline` and `_sheet_xml_name`
are lifted from private helpers to importable functions in `fill.py` (no behaviour change), and the new
save path calls them. This is covered by a test asserting no `t="s"` cells remain in the `Sarees` sheet
after an in-app save.

### 6.3 Vocabulary guard

Before writing, every non-empty submitted value is checked for exact membership in that column's
`vocab_by_header` list. A value outside the vocabulary is never written; the request fails with an error
panel naming the column. The UI cannot produce such a value — this guards against a stale open tab, a
hand-crafted POST, or a template swap, and encodes the owner's "no deviation from Myntra vocabulary" rule
in code rather than only in markup.

## 7. Components

| Component | Responsibility |
|---|---|
| `src/myntra/attribute_entry.py` (new) | Pure logic: load `user_filled_attributes`; build `{column: [vocab]}` for the 12; validate submitted values against vocab; `write_attributes()` (§6). No FastAPI imports. |
| `src/myntra/preview.py` (edit) | Gains `build_card()`; existing reconstruction functions unchanged. |
| `src/myntra/fill.py` (edit) | `_shared_to_inline` / `_sheet_xml_name` become public (`shared_to_inline`, `sheet_xml_name`); behaviour unchanged. |
| `src/web/routers/attributes.py` (new) | `GET /generate/attributes/{job_id}` (screen), `POST /generate/attributes/{job_id}/preview` (live fragment), `POST /generate/attributes/{job_id}` (save). Auth via `get_user`. |
| `src/web/routers/preview.py` (edit) | Uses `build_card()` and the shared vocab/rules loader. |
| `src/web/main.py` (edit) | Register the new router. |
| Templates | `attributes.html` (page + accordion), `_attr_panel.html` (one SKU), `_preview_card.html` (shared card, extracted), `_attr_saved.html` (post-save confirmation); `_result.html` gains the "Fill attributes" action. |
| `config/myntra/rules.yaml` (edit) | +3 columns in `user_filled_attributes`. |

### 7.1 Data joins

Everything keys off the job directory `src/web/runtime/{job_id}/`, which already holds both inputs:

- **Rows** ← `read_filled_rows(job.result["filled"], template)` → row ordinal + `vendorSkuCode` + any values
  already saved (so re-opening the screen shows previous choices selected).
- **Photo + title** ← `read_products(job_dir/products_export.csv)` → `Product.sku` → `Product.images[0]`,
  `Product.title`.
- **Sheet row** ← `template.first_data_row + ordinal`, SKU-verified on write (§6 step 2).

Form fields are named `attr__{row_ordinal}__{column_ordinal}` with a hidden `sku__{row_ordinal}` per panel —
positional indices avoid escaping SKUs and column names into field names, and the hidden SKU is what the
write-time verification checks.

## 8. Testing (TDD)

Logic tests (`tests/`, no web):

- Vocabulary loads for all 12 columns from V13 and is non-empty; `NA` present exactly where the template has
  it (per the §3 table) and never injected where it is not.
- `write_attributes` writes the right cells on the right row; unselected columns remain `None`; a second save
  overwrites; clearing to the placeholder blanks the cell.
- **Dropdown survival:** build → save attributes → reload; the `Sarees` sheet's list validations are
  unchanged in count and still cover the 12 columns.
- **Inline strings:** after an in-app save, the `Sarees` sheet XML contains no `t="s"` cells.
- Vocabulary guard: an off-vocabulary value raises and writes nothing.
- SKU-mismatch guard: a tampered ordinal/SKU pair raises and writes nothing.
- `build_card()` output for a given attribute dict equals what `/preview` produced for the same row.

Web tests (existing FastAPI TestClient style):

- `GET` the screen renders one panel per SKU, 12 selects per panel, options only from vocab, image src from
  the Shopify export, placeholder for a product with no images.
- Live-preview `POST` returns the reconstructed title/details for the posted values.
- Save `POST` writes the file and returns the confirmation panel; downloading afterwards serves the updated
  file.
- Expired/unknown job → the existing "session expired, please re-upload" 404 behaviour.
- Existing `/preview`, generate, fix and pipeline tests stay green (the 3 new columns widen preview
  expectations — update those assertions).

Manual gate (owner-run, unautomatable): one real batch filled in-app, downloaded, opened in Excel to confirm
dropdowns are live, then uploaded to Myntra to confirm acceptance.

## 9. Edge cases

- **No image for a SKU** → placeholder tile; everything else works.
- **Job expired / app restarted** (in-memory `JobStore`) → existing "session expired, please re-upload".
- **Re-save** → overwrites; the screen re-opens showing current values selected.
- **Screen skipped** → file downloads blank + dropdowns exactly as today.
- **Mixed workflow** → fill some in-app, download, fill the rest in Excel. Both write the same cells, so the
  two paths compose.
- **Empty batch (0 rows)** → screen shows "nothing to fill" and links to download.

## 10. Risks

- **Dropdown loss on re-save** is the main risk; mitigated by the explicit round-trip test (§8) which fails
  loudly if openpyxl behaviour ever changes.
- **Shared-string regression** (§6.2) would be invisible in the app and only surface as a Myntra upload
  rejection; mitigated by the `t="s"` test.
- **Per-keystroke request volume** — one request per dropdown change; fine for a single-user app, and the
  fragment is small. If it ever becomes a problem, the fallback is debouncing, not client-side logic.
- **In-memory job state** means a server restart loses an unfinished fill session. Accepted (same as every
  other job-scoped screen in this app); the owner can re-generate or fill in Excel.
