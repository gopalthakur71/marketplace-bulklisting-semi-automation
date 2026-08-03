# HSN from the Shopify export, plus Stop and Clear on Generate

**Date:** 2026-08-03
**Branch:** `feat/generate-stop-clear`

Two independent changes to the Generate flow were designed together. Their status now
differs:

1. **HSN comes from Shopify** — ⏸ **DEFERRED 2026-08-03, not being built.** The existing
   HSN knowledge-base flow stays exactly as it is, untouched. The owner has a separate
   plan for the `custom.hsn` metafield and will revive this later. Part 1 below is
   retained as a worked-out design to pick up from, not as work in progress.
2. **Stop and Clear buttons** — ✅ **Approved for planning.** A Stop button halts a build
   that is already running; a Clear button empties the chosen CSV and the panel below the
   form. This is the whole of the current scope.

**Standing constraint on Part 2:** nothing in it may disturb the HSN flow or any other
part of the pipeline. The existing HSN pre-scan, review screen, and
`hsn_by_signature` threading must keep working unchanged, and the full suite must stay
green.

---

## Background

Until now the app could not get HSN from Shopify. The Harmonized System code lives on
the *variant's* Shipping tab in the Shopify admin, and that field is simply **not one of
the product CSV export's columns** — verified on 2026-07-27 against a real export
(`TurquoiseBlue.csv`, the same 61 columns as `input/products_export.csv`, no HS/HSN
column anywhere). Country of Origin has the same problem.

The workaround was the HSN knowledge base: the app asked once per `category|fabric`
signature and remembered the answer. That is the "One-time HSN codes for this batch"
screen.

What *does* survive the export is **product metafields**, already visible in the file as
`Color (product.metafields.shopify.color-pattern)`, `Fabric (...)`, `Size (...)`. A
custom product metafield `custom.hsn` has now been created and filled, and it appears in
the export. That closes the gap, so the on-screen asking can go.

**Variant metafields do not export either** — the metafield must stay product-level.

---

## Part 1 — HSN from the export ⏸ DEFERRED

> **Not being built.** Deferred on 2026-08-03 by the owner, who has a separate plan for
> the metafield. Everything below is a finished design held for later; no code in this
> section is to be written now, and the current HSN knowledge-base flow stays live and
> untouched. The one item that was still open when it was parked: the exact export
> header string must be confirmed against a real export before any of this is built.

### 1.1 Reading the column

`src/core/shopify_reader.py` gains a fourth metafield constant alongside the existing
three:

```python
HSN_COL = "HSN (product.metafields.custom.hsn)"
```

> **Open item:** the exact header string must be confirmed against a fresh export before
> implementation. Shopify derives it from the metafield's *name* and namespace/key, so a
> metafield named something other than "HSN" produces a different header. The
> implementation plan's first step is to read the header row of the user's current export
> and pin this constant to what is actually there.

`src/core/models.py` gains `hsn: str | None` on `Product`, populated via the existing
`fv()` helper, which already returns `None` for an absent column. An export without the
column therefore reads cleanly and every product simply has `hsn=None` — that condition
is what the gate below rejects, with a clear message, rather than a crash.

### 1.2 Validating

New module `src/myntra/hsn_source.py`, deliberately small and pure:

```python
def check(products) -> list[dict]
```

Returns one entry per product whose HSN is unusable, each `{"sku", "title", "found"}`,
where `found` is the raw value seen or `None` when blank. Empty list means the batch is
good.

A value is usable when, after stripping surrounding whitespace, it matches `\d{8}`
exactly. This is the same 8-digit rule the removed on-screen form enforced, so the
standard does not change — only where the number comes from. Anything else — blank,
`None`, 4-digit `5407`, `6211.42.90` with punctuation, stray text — is reported.

The module has no knowledge of web requests, jobs, or Shopify; it takes products and
returns findings.

### 1.3 Blocking the build

In `POST /generate` ([generate.py](../../../src/web/routers/generate.py)), the gate runs
**after** the duplicate-SKU guard and **in place of** `_hsn_prescan_or_build`. The same
gate applies on the `POST /generate/new-only/{job_id}` path, checked against only the
SKUs that path is about to build.

If `check()` returns anything:

- no job thread is spawned
- **no styleGroupIds are reserved** — `reserve()` is not called, so the counter is untouched
- a new partial `_hsn_missing.html` renders the offending SKUs in a table: SKU, product
  title, and the value found (an em dash when blank)
- the panel explains the fix — set `custom.hsn` on those products in Shopify, re-export,
  upload again — and offers **no** way to continue

This is a hard block by explicit decision: a sheet with blank HSN is rejected by Myntra
at upload anyway, so generating one wastes a round trip.

If `check()` returns nothing, the flow proceeds directly to `_start_build`, which is
what `_hsn_prescan_or_build` used to do only after the user filled the form.

### 1.4 Mapping

`map_product` currently takes `hsn_by_signature` and computes a `category|fabric`
signature to look the code up. That parameter is replaced by a plain `hsn` value:

```python
def map_product(product, template, column_map, constants, rules=None,
                hsn=None, hsn_override=None):
```

Precedence, unchanged in spirit:

1. `hsn_override` — a per-SKU code pinned from the SKU registry, used by the fix flow's
   deterministic rebuild. Still wins over everything.
2. `hsn` — the value from the export, passed in by the pipeline.
3. Neither — the HSN cell is left blank and no flag is raised. This is the CLI path and
   the dedup-scan path, both of which intentionally run without HSN.

The `from src.myntra.hsn_kb import signature` import is dropped from `mapper.py`. The
"no HSN learned for signature" flag disappears with the lookup it described; the gate in
1.3 now catches that case earlier and more clearly.

`pipeline.main` replaces its `hsn_by_signature` parameter with nothing — it reads
`p.hsn` from each product and passes it through — while **keeping** `hsn_by_sku`, which
the fix-flow rebuild depends on.

### 1.5 The dedup-hash trap

`scan_content_hashes` in [pipeline.py](../../../src/myntra/pipeline.py) computes the
content hash that the duplicate-generation guard compares against the SKU registry. Its
docstring is explicit that it runs "with HSN unset" — the hash is deliberately
independent of HSN.

If HSN now flows from the product into the mapped cells, this function would start
hashing it, **every SKU already in the registry would hash differently, and the next
upload would report the entire catalog as "edited."**

So `scan_content_hashes` explicitly passes no HSN (`map_product(..., hsn=None)`) and
keeps its current behaviour. This is why 1.4 makes "neither argument given" mean "leave
HSN blank" rather than an error. A test pins this: the hash of a product with a
populated HSN must equal the hash of the same product without one.

### 1.6 What is removed, and what stays

**Removed (wiring):**

| Thing | Where |
|---|---|
| `_hsn_prescan_or_build` | `src/web/routers/generate.py` |
| `POST /generate/hsn/{job_id}` (`hsn_submit`) | `src/web/routers/generate.py` |
| `_hsn_review.html` | `src/web/templates/` |
| per-job `hsn.json` write/read | `src/web/routers/generate.py` |
| `hsn_by_signature` parameter | `mapper.map_product`, `pipeline.main` |
| `signature` import | `src/myntra/mapper.py` |
| tests exercising the removed route and the signature-driven mapper path | `tests/test_mapper.py`, `tests/test_pipeline_override.py`, `tests/web/test_generate.py` |

A full-tree search confirms nothing else imports the knowledge base: the other modules
matching "hsn" (`fix.py`, `corrector.py`, `fill.py`, `template_guard.py`) reference only
the sheet's plain `HSN` column, which is unaffected.

**Kept, unused, by explicit decision:**

`src/myntra/hsn_kb.py` and `settings.hsn_store` / `HSN_LOCAL_PATH` stay on disk with
their own tests (`tests/test_hsn_kb.py`, `tests/test_signature.py`) still passing, so the
knowledge base remains a working fallback if the metafield approach disappoints. Nothing
imports them from the request path. A short comment at the top of `hsn_kb.py` records
that it is retained deliberately and is not wired in, so a future reader does not treat
it as dead code to delete or as live code to maintain against.

**Kept and load-bearing:**

The SKU registry's per-SKU `hsn` field, and `hsn_by_sku` through `pipeline.main`. The
fix flow's rebuild pins the original HSN of already-listed SKUs from the registry; that
must not regress.

---

## Part 2 — Stop and Clear

### 2.1 Clear

A `Clear` button next to the file input on `generate.html`. Entirely client-side, no
request:

- resets the file input's `value`, so the drop zone returns to its empty state
- empties `#progress`, the panel below the form

Per explicit decision, Clear wipes the whole panel — including a finished result and its
download link. The button is styled as secondary so it does not compete with Generate,
and its title attribute notes that a finished result will be cleared too. The generated
file itself is untouched on disk; only the on-screen panel is emptied.

### 2.2 Stop

A `Stop` button sitting **directly beside Generate** in the form's button row, as
requested, present only while a build is actually running.

**Placement.** The form gains an empty `<span id="run-controls">` next to the Generate
button. The stepper response carries an out-of-band fragment
(`hx-swap-oob="innerHTML:#run-controls"`) that fills it with the Stop button; the result,
error, and cancelled panels carry the same fragment empty, which removes it.

Because Stop sits *inside* the upload form, it must carry `hx-params="none"`. htmx posts
an enclosing form's values by default, and `hx-encoding="multipart/form-data"` is
inherited — without it, every Stop click would re-upload the whole CSV just to say
"stop". A test pins this.

This rides the polling the stepper already does — `hx-get="/jobs/{id}"` every second —
so the control is re-asserted once a second while running and cleared within a second of
the run ending. That makes it self-healing rather than fragile: a single missed swap
corrects itself on the next poll. It is the reason for choosing OOB over toggling
visibility with client-side state, which would have no such recovery.

**Signal.** `JobStore` gains a `cancel_requested` flag per job and a
`request_cancel(job_id)` method, guarded by the existing lock. `POST
/generate/cancel/{job_id}` sets it and immediately returns the stepper, so the panel
shows "Stopping…" while the worker winds down.

**Checking.** `pipeline.main` gains an optional `should_cancel=None` callable, checked at
three points:

1. the top of each product iteration
2. before `fill_template` writes the xlsx
3. before the S3 upload

When it returns true, the pipeline raises `BuildCancelled` (a new exception in
`src/myntra/pipeline.py`). Cancellation is therefore **cooperative and bounded by one
product** — a Stop pressed mid-way through downloading a six-image product takes effect
when that product finishes. This is deliberate: killing the worker thread mid-write
risks a corrupt xlsx and a half-uploaded S3 batch, and the boundary wait is short.

When `should_cancel` is `None`, nothing changes — the CLI path and every existing test
keep their current behaviour.

**Landing.** `_run_generate` catches `BuildCancelled` separately from the existing
`except Exception` (which must keep reporting real failures as errors, not cancellations)
and:

- deletes a partially written `myntra_filled.xlsx` if one exists
- marks the reserved ledger batch `cancelled` via a new
  `groupid_ledger.cancel(store, batch_id)`, so it does not sit in the ledger as `pending`
  forever
- sets job status to `cancelled`

`groupid_ledger.cancel` mirrors `confirm` in shape: it finds the batch by id, requires
status `pending`, sets it to `cancelled`, and — critically — **does not touch
`next_style_group_id`**. Because `reserve()` never advances the counter (only `confirm`
does), a cancelled build costs nothing. Cancelling an already-confirmed batch raises, as
`confirm` does for the mirror case.

No SKU registry records are written, and this needs no new code: `_run_generate` only
calls `record()` after `pipeline_main` returns, which a cancelled run never does.

**Showing.** `GET /jobs/{job_id}` renders a new `_cancelled.html` when status is
`cancelled`. It states plainly that the build was stopped and that nothing was
recorded — no styleGroupIds used, no SKUs registered — and links back to the upload form.
Wording matters here: the user needs to know with certainty that a stopped run left no
trace, so they can simply upload again.

---

## Testing

Part 2 only. Part 1's tests are deferred with its design.

| Area | Test |
|---|---|
| Cancel | `request_cancel` flips the flag; pipeline raises `BuildCancelled` at a product boundary via a stub `should_cancel`; `should_cancel=None` changes nothing |
| Cancel | job ends `cancelled` not `error`; partial xlsx deleted; ledger batch `cancelled`; `next_style_group_id` unchanged; zero registry records |
| Ledger | `cancel` on a pending batch works; on a confirmed batch raises |
| Templates | the stepper response carries the Stop OOB fragment; result/error/cancelled responses carry it empty; Clear resets the input and empties `#progress` |

**Regression bar.** The whole existing suite must stay green, with particular attention
to the HSN path this work deliberately leaves alone: `tests/test_hsn_kb.py`,
`tests/test_signature.py`, `tests/test_pipeline_override.py`, `tests/test_mapper.py`,
and `tests/web/test_generate.py`. `should_cancel` defaulting to `None` is what makes
that cheap — every existing caller of `pipeline.main` keeps its exact behaviour.

---

## Out of scope

- Country of Origin from Shopify. It has the same export limitation as the HS code and
  is filled from constants today. Not part of this work.
- The Shopify Admin API route. The metafield makes it unnecessary.
- The blank `Fabric` metafield spotted on a saree in July. Fabric no longer feeds an HSN
  signature once this ships, which removes the urgency, but it is worth a separate look.
- Retro-filling HSN for SKUs already in the registry. Their HSN is already pinned there
  and the fix-flow rebuild uses it.
