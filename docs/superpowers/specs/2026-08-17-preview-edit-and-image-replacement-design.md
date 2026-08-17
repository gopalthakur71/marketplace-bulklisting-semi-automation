# Spec: Editable Preview + Product Image Replacement

**Project:** Myntra bulk-listing pipeline (Shopify CSV → Myntra template, sarees only)
**Type:** New editing surface (re-uses the Fill-attributes screen) + one new capability (replace product images)
**Date:** 2026-08-17
**Branch:** `feat/preview-edit-image-replace`
**Builds on:** `2026-07-24-myntra-attribute-excel-roundtrip-preview-design.md` (Feature B, the read-only
preview) and `2026-07-26-in-app-attribute-entry-design.md` (Flow D, the Fill-attributes screen).

---

## 1. Problem

Two problems, reported by the owner on 2026-08-17 from real Myntra runs.

**1a. A filled sheet cannot be re-opened for editing.** Job ids live only in memory
(`src/web/jobs.py`), so the Fill-attributes screen is reachable only from a generate result in the
current server process. Come back after a restart — or fill the sheet in Excel rather than in the app —
and the only surface left is `/preview`, which is read-only. The owner's actual working pattern is
"open the sheet I filled earlier, check it over, change the one attribute that looks wrong", and the
app has no way to do that.

**1b. Myntra rejects product images, and the app dead-ends.** `config/myntra/error_rules.yaml` already
recognises three image rejections — `flat shot`, `pixelated`, `incorrectly cropped` — and each is
`action: explain_only` with text that says, in effect, *replace the photo and re-upload; the app cannot
fix photos*. The diagnosis is right and the remedy is unreachable: there is nowhere in the app to put a
new photo. The owner must re-shoot, re-import through Shopify, and regenerate.

## 2. Goals / Non-goals

**Goals**

1. Any filled Myntra workbook can be uploaded and **edited in the app** — attributes, names, HSN —
   with the same validation, vocabulary and registry pinning as a freshly generated one.
2. Product images can be **replaced per slot** from local files, converted, validated and hosted, so a
   Myntra image rejection has an in-app fix.
3. A **Clear** control returns the Preview screen to an empty upload box, so checking several files in
   a row is one click between them.

**Non-goals (explicitly dropped)**

- ❌ **Editing the SKU in the app.** The owner's decision (2026-08-17): he will edit SKUs in Excel after
  download. See §10.1 for why this was the expensive half, and §10.2 for the manual-edit hazard.
- ❌ Sessions that survive a server restart. Upload → edit → download; the downloaded file is the
  durable artefact.
- ❌ Any change to how titles/descriptions are reconstructed, to styleGroupId assignment, or to the
  dedup guard.
- ❌ Image *editing* (crop, retouch, resize beyond the existing JPG flatten). The app accepts the photo
  it is given and validates it.

## 3. Core mechanism: adopt an uploaded workbook as a job

Every screen downstream of a build — the attribute panels, the vocabulary dropdowns, the live preview
card, per-panel save, the HSN gap banner, registry pinning, download — depends on exactly one thing: a
job in the store whose `result["filled"]` is a workbook on disk. Nothing in that chain cares where the
workbook came from.

So `POST /preview` stops writing a throwaway temp file and instead:

1. Validates the upload is `.xlsx` and that `read_filled_rows` finds at least one row with a
   `vendorSkuCode`. A file that yields no rows is rejected with a plain message and **no job is
   created** (§7.2).
2. Creates a job via `store.create()`, writes the upload to `RUNTIME/{job.id}/filled.xlsx`, and calls
   `store.finish(job.id, {"filled": path, "origin": "upload", "filename": <original name>})`.
3. Renders the existing Fill-attributes screen for that job.

**Consequences, all of them wanted:**

- `/generate/download/{job_id}` works unchanged — it needs only `result["filled"]`
  (`src/web/routers/generate.py:267-273`).
- Per-panel and bulk save work unchanged, including the HSN and name pins into `sku_registry`
  (`src/web/routers/attributes.py:229-240`), which the owner confirmed he wants on this path too.
- `write_attributes` re-applies `shared_to_inline` after openpyxl re-saves, so an edited upload stays
  parseable by Myntra. This trap is already locked by `test_write_attributes_keeps_strings_inline`.

### 3.1 Photo fallback

`_panels` currently takes the panel photo from the job's `products_export.csv`. An adopted upload has
no CSV. Add a fallback: use the CSV product when present, else the sheet's own `Front Image` column,
which the pipeline fills with a public S3 URL (`src/myntra/fill.py:10`). Where neither exists, the
existing "no photo" placeholder stands.

### 3.2 Screen chrome

The heading and hint switch on `result["origin"]`:

| origin | heading | hint |
|---|---|---|
| generate | Fill attributes | (today's text) |
| upload | Preview & edit — *filename.xlsx* | "Editing your uploaded copy. Download when you're done — the app keeps nothing after a restart." |

The owner must always be able to tell **which file** he is editing. The filename is displayed, escaped.

## 4. Image replacement

### 4.1 The panel block

A new block inside each attribute panel, one row per Myntra image slot, in template order:
`Front Image`, `Side Image`, `Back Image`, `Detail Angle`, `Look Shot Image`, `Additional Image 1`,
`Additional Image 2` (`src/myntra/fill.py:10`).

Each row shows the current image as a thumbnail (or "empty"), and a file input. **Choosing a file is
what selects a slot** — there is no separate checkbox to keep in sync with the picker. A slot with no
file chosen is left completely untouched, so replacing one rejected shot and re-supplying all seven
after a re-shoot are the same code path.

### 4.2 The write path

For each supplied file, in slot order, re-using what already exists in `src/core/images.py`:

1. `flatten_to_jpg(img, quality, out_path)` — flattens transparency onto white, converts to JPEG at the
   configured quality.
2. `validate_image(path, specs)` against `config/myntra/image_specs.yaml`: `min_width: 700`,
   `min_height: 700`, `max_bytes: 10485760`.
3. `upload_images(...)` from `src/core/s3_upload.py` to the configured bucket/prefix.
4. Write the resulting public URL into that slot's column in the workbook.

### 4.3 The cache-bust key — the decision that makes this work at all

Today's S3 key is `{sku}/{n}.jpg` (`src/core/images.py:46`). A replacement uploaded under that scheme
overwrites the object and produces a URL **byte-identical to the one Myntra already rejected**. If
Myntra caches by URL, the new photo is never fetched and the whole feature silently does nothing.

Replacement images are therefore written to a content-addressed key:

```
{sku}/{n}-{sha256(file_bytes)[:8]}.jpg
```

A genuinely different photo yields a different URL. Re-uploading the identical file twice is
idempotent rather than accumulating junk objects. The original generate path is **unchanged** — only
replacements use the suffixed key, so nothing about existing batches shifts.

### 4.4 Per-slot outcomes

Validation is reported per slot, not per panel. A 500×500 photo fails its own slot with
`dimensions 500x500 below minimum 700x700`; the slots that passed are still written and their URLs
still land in the sheet. The response lists each attempted slot and what happened to it, so a partial
success is legible rather than mysterious.

### 4.5 Ordering against attribute saves

Image replacement and attribute saving both re-write the same workbook, so image writes take the same
`_WRITE_LOCK` that attribute saves use (`src/web/routers/attributes.py:34`). Two concurrent writes
would otherwise interleave and one would be silently lost.

## 5. Placement — one component, three entry points

The owner asked for image replacement on Preview, on Fill attributes, and on Fix errors.

- **Preview and Fill attributes are the same screen** under §3, so both are satisfied by building the
  block once, inside the panel.
- **Fix errors** gets an entry point rather than a duplicate copy of the component. On a fix result,
  issues whose `category` is `image` gain a button — *"Replace images for these N SKUs →"* — which
  adopts the corrected workbook the fix run produced (`/fix/download/{fix_id}`'s file) as a job by the
  §3 mechanism, and opens it filtered to the affected SKUs.

This keeps a single implementation of validation, hosting and writing. Duplicating the block into the
fix templates would re-introduce exactly the drift that sharing `build_card` was introduced to prevent.

**Sequencing note:** the fix flow's corrected workbook exists only *after* apply, so the button belongs
on the fix result, not on the pre-apply error listing. Image issues are `explain_only`, and an
explain-only-only run already has a "Download listing file" path; the button attaches at the same point.

## 6. Clear

`POST /preview/clear/{job_id}` drops the job from the store, deletes its runtime directory, and swaps
the empty upload form back in.

Per the owner's description of his two flows: checking a file and finding it correct is the common
case, so Clear is **instant** when no save has occurred in this session. If a save has occurred and no
download has followed, it asks once — *"You've saved edits you haven't downloaded. Discard them?"* —
because the server copy is the only copy of that work.

## 7. Guards and failure handling

1. **S3 not configured.** If `public_base_url` or the bucket is absent, image replacement refuses with
   a clear message and writes nothing. It must never write a local filesystem path into a column that
   Myntra reads as a URL. (The generate path's existing fallback — use the source CDN URL — is
   meaningless here: a browser-uploaded file has no source URL.)
2. **Unreadable workbook.** No rows carrying a `vendorSkuCode` → plain error, no job created, upload
   box preserved. This catches the wrong-file case (a Shopify CSV, last year's template) before it
   presents as an empty accordion.
3. **Non-image upload.** Files that PIL cannot open fail their slot with `convert error: …`, exactly as
   the existing pipeline reports a bad download.
4. **Escaping.** The original filename is user-controlled text rendered into HTML; Jinja autoescape
   covers it, and the existing `/fix/apply` XSS fix is the precedent to follow.

## 8. Phasing

Three phases, each independently useful and independently testable. The owner can run the app and
judge each before the next begins.

| Phase | Delivers | Usable on its own? |
|---|---|---|
| 1 | §3 adoption + §3.1 photo + §3.2 chrome + §6 Clear | Yes — re-open and edit any filled sheet, the original ask |
| 2 | §4 image replacement inside the panel | Yes — the Myntra image-rejection fix |
| 3 | §5 Fix-errors entry point | Convenience; phase 2 already covers the need via Preview |

## 9. Testing

TDD throughout. Targeted files only, per the owner's standing instruction — the full suite is not run:

| Area | File | Cases |
|---|---|---|
| Adoption | `tests/web/test_preview.py` | upload → job created, panels render; rowless file → error, no job; download serves the edited file |
| Photo fallback | `tests/web/test_attributes.py` | photo from CSV when present; from `Front Image` when not; placeholder when neither |
| Chrome | `tests/web/test_preview.py` | upload-origin shows filename; generate-origin unchanged |
| Image write | `tests/myntra/test_images.py` (or new `test_image_replace.py`) | content-addressed key; undersized image fails its slot only; S3 absent → refuses |
| Inline strings | existing | `shared_to_inline` still holds after an image write |
| Clear | `tests/web/test_preview.py` | job dropped, directory removed; confirm required after a save |
| Fix entry | `tests/web/test_fix.py` | image-category result offers the button; adopts the corrected file |

## 10. Notes carried from the design conversation

### 10.1 Why SKU editing was dropped

Not arbitrary scope-cutting — three real costs. `write_attributes` deliberately verifies every row's
SKU before writing, so editing the SKU means carving an exception into a safety mechanism whose job is
to prevent writing to the wrong row. The `sku_registry` entry — content hash, styleGroupId, HSN pin,
name pins — is keyed by SKU, so a rename orphans it. And S3 image keys derive from the SKU. The owner
judged the Excel route cheaper, and it is.

### 10.2 Hazard for the owner's manual SKU edits

The SKU is written into **three** columns, not one: `vendorSkuCode` (`config/myntra/column_map.yaml:5`),
`SKUCode` and `vendorArticleNumber` (`src/myntra/mapper.py:143-144`). A manual edit must change all
three or Myntra receives an inconsistent identity. This is worth a line in
`docs/APP-FEATURES-GUIDE.md`.

### 10.3 Open question, not resolved by this spec

Myntra's "style already present" rejection: the owner reports Myntra's own support advised *"just
change the SKU a little"*, and that the `styleId` column exists but Myntra does not use it. Whether a
rejected style leaves a live listing behind "varies / not sure". This spec therefore does **not** claim
to solve duplicate-style rejection; it delivers image replacement, which is well-understood, and leaves
the SKU lever in the owner's hands. If a pattern emerges from future rejection reports, revisit —
possibly as a rule in `error_rules.yaml`, which is where `already registered` already lives as
`drop_sku`.

## 11. Files expected to change

| File | Change |
|---|---|
| `src/web/routers/preview.py` | upload adopts a job; clear route; rowless guard |
| `src/web/routers/attributes.py` | photo fallback; origin-aware chrome; image save route |
| `src/web/jobs.py` | a way to drop a job |
| `src/myntra/image_replace.py` *(new)* | content-addressed key, per-slot validate/host/write |
| `src/web/templates/preview.html` | upload box + Clear |
| `src/web/templates/_attr_panel.html` | the image slot block |
| `src/web/templates/attributes.html` | origin-aware heading/hint |
| `src/web/routers/fix.py` + fix templates | the "Replace images" entry point |
| `docs/APP-FEATURES-GUIDE.md`, `docs/ARCHITECTURE.md`, `AGENTS.md` | new flow + the three-column SKU note |
