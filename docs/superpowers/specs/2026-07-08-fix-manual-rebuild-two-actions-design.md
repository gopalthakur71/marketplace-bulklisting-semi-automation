# Fix Review Screen — Two Actions + Manual-Fix Rebuild — Design

**Date:** 2026-07-08
**Status:** Approved (verbal, brainstorming). Build in a later session.
**Branch:** `feat/fix-manual-rebuild` (off `main` — build fresh; the Surface-B export fix it builds on is already merged as 161c69c).
**Builds on:** [[fix-surface-b-export-bug]] — the shared export upload, the `regenerate_surface_b(..., csv_path=)` rebuild, the try/except → 200 error panel, and the "upload your products export" prompt already exist and are reused wholesale.

---

## Why

On the Fix review screen the rejected SKUs split into two groups:

- **"We can fix these"** — auto-fixable (address, pincode, MRP/ISP). The app rebuilds them.
- **"You must fix these yourself first"** — need real off-app work (re-shoot photos, quality, resolution). Today this group is **display-only**: the app explains but offers no output for it.

Gopal wants both groups to end in a **ready-to-upload Myntra sheet**. The manual-fix group's real workflow is: fix the photo in Shopify → re-export those SKUs → have the app rebuild a Myntra sheet for **just those SKUs**, keeping the **same HSN and styleGroupId as the first attempt**. That HSN consistency matters: if the HSN differs from the first listing attempt, Myntra rejects it. The app already pins HSN/styleGroupId per SKU in the **SKU registry** (recorded at generate time), so a scoped rebuild produces exactly that — the fixed image flows in, identity fields stay stable, nothing unintended drifts.

This is why the flow lives on the **Fix page** and not the normal Generate flow: Generate would trip the pre-upload duplicate-SKU guard (these SKUs are already in the registry). The Fix page is the intended "re-list these known, rejected SKUs" path.

## Decisions (approved)

- **(a)** Both groups get an action button. Correctable → **"Fix & download now"** (today's Proceed, relabeled, scoped to the correctable SKUs; behavior unchanged). Manual-fix → **"Download listing file for these SKUs"** (new).
- **(b)** The manual-fix button rebuilds **only the explain-only SKUs** via the existing `regenerate_surface_b(explain_only_skus, settings, fix_dir, csv_path=<export>)`. Registry pins HSN + styleGroupId automatically. It produces its own corrected `.xlsx` + download, independent of the correctable fix.
- **(c)** One **shared Shopify-export upload** serves both actions. For the manual-fix flow this is the **fresh** export (re-shot photos already uploaded to Shopify, those SKUs re-exported).
- **(d)** The two buttons submit the same form; the **clicked submit button's own `name="action"` value** (`fix` | `manual`) tells the server which SKU set to rebuild (only the pressed button's name/value is posted).
- **(e)** The manual-fix group carries a **clear guidance message** describing the reshoot → Shopify → re-export → download workflow. (Explicitly requested.)
- **(f)** No new image path. Images stay sourced from the Shopify export (Model A). Direct photo-upload-to-S3-by-SKU (Model B) was considered and **rejected** for this iteration — see Out of scope.

---

## 1. UI — `src/web/templates/_fix_review.html`

Single `<form hx-post="/fix/apply/{{ fix_id }}">` as today, with:

- **Correctable group** unchanged, but its submit button becomes:
  `<button name="action" value="fix">Fix &amp; download now →</button>` (shown only if `correctable`).
- **Manual-fix (explain-only) group** gains, after the cards:
  - The **guidance message** (copy below).
  - `<button name="action" value="manual">Download listing file for these SKUs →</button>` (shown only if `explain_only`).
- **Shared export input** (`products_export`, visible, `required`, `.csv`, multipart) shown whenever `needs_export` — see §3. Keep it visible (never `required` on a hidden input — [[web-ui-gotchas]]).
- **"Do not make any changes"** stays.

Both submit buttons live inside the one form, so either submits the shared export + the `action` value.

## 2. Guidance copy (manual-fix group)

> **These need a real fix the app can't do (photos, image quality, resolution).**
> Fix them in **Shopify first** — re-shoot and upload the corrected photos onto the product — then **re-export just these SKUs** from Shopify and drop that file in the box above.
> Click **Download listing file** and the app rebuilds a ready-to-upload Myntra sheet for these SKUs with your new images, keeping the **same HSN and style group** as your first attempt so Myntra won't reject them again.

## 3. Behavior — `src/web/routers/fix.py`

- **`fix_upload`**: compute
  `needs_export = (source_type in ("sheet_csv","listings_report") and bool(correctable)) or bool(explain_only)`.
  (Either a Surface-B correctable fix or any manual-fix rebuild needs the export.) Pass `needs_export` to the template as today.
- **`_fix_apply`**: read the `action` field from the form (default `"fix"`).
  - **`action == "manual"`**: scope the rebuild to the explain-only SKUs:
    `skus = sorted({i.sku for i in issues if i.sku and i.action == "explain_only"})`.
    Require the export (reuse `_save_export` + `_export_prompt_panel`; no export → prompt, no rebuild). Then `regenerate_surface_b(skus, settings, fix_dir, csv_path=csv_path)`, copy the file to `out_path`, surface `manual_needed`/`could_not_rebuild` as today, render `_fix_result.html`.
    - Empty `skus` (no explain-only with a real SKU) → the existing "nothing to rebuild" short-circuit result.
  - **`action == "fix"` (default)**: the current behavior, unchanged (Surface A in-place for `sku_xlsx`; Surface B correctable rebuild otherwise).
- **Registry pinning** is automatic inside `regenerate_surface_b` (reads `sku_registry_store`); SKUs absent from the registry come back in `could_not_rebuild` and are shown on the result screen.
- **Error handling** unchanged: the `fix_apply` try/except wrapper still returns a 200 escaped error panel for any exception; `HTTPException` (404) still passes through.

## 4. Where it plugs in

- `src/web/templates/_fix_review.html`: two named submit buttons; guidance block; `needs_export` gate widened.
- `src/web/routers/fix.py`: `needs_export` formula; `action` branch in `_fix_apply`; explain-only SKU scoping. No new endpoints, no new templates, no pipeline changes.

## Testing (TDD — failing tests first)

Web (`tests/web/test_fix.py`):
- Upload with explain-only items → response shows the **"Download listing file"** button **and** the guidance copy **and** the `products_export` input (even when there are no correctable items).
- `action=manual` with export → `regenerate_surface_b` called with **exactly the explain-only SKUs** and a real `csv_path` holding the uploaded bytes; result screen offers the download.
- `action=manual` **without** export → prompt panel, `regenerate_surface_b` **not** called.
- `action=manual` with no explain-only SKU carrying a real SKU → "nothing to rebuild" result, no rebuild.
- `action=fix` (default) still rebuilds **only correctable** SKUs — manual-fix SKUs excluded (regression guard on the existing behavior).
- `needs_export` true when explain-only present regardless of correctable.

Reuse the existing real-pipeline e2e pattern in `tests/web/test_fix_e2e.py` if a genuine manual-rebuild end-to-end is cheap to add (upload rejection + fixture export, `action=manual`, download a valid xlsx). Optional but preferred — it guards the same coverage gap that bit us before.

## Out of scope

- **Model B — direct photo upload to S3 by SKU.** No app UI to push image files to S3 keyed by SKU; images stay sourced from the Shopify export. Revisit only if the Shopify round-trip proves too slow in practice.
- Surgical single-cell edits (the rebuild regenerates the whole row for those SKUs, pinning identity fields — accepted as equivalent for our purpose).
- Any change to the correctable ("Fix & download now") behavior beyond the button label.
- CLI parity.
