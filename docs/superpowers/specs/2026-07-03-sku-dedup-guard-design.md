# Duplicate-Upload Guard (SKU Registry) — Design

**Date:** 2026-07-03
**Status:** Approved design, pending spec review
**Branch:** `feat/hsn-knowledge-base` (ships with the HSN KB + backlog-fix batch)
**Related:** `docs/superpowers/specs/2026-07-02-hsn-kb-and-app-fixes-design.md` — shares the Generate "pre-scan → review → build" machinery.

---

## Why

Re-running the app on a products export that was already processed silently regenerates the sheet and re-issues `styleGroupId`s, with no warning. Gopal hit this via `python run.py` (the CLI path, which doesn't even touch the ledger). Nothing tells him "these products are already done — regenerate?" The app needs a duplicate guard, keyed on SKU, based on the app's own records.

### Grounding facts (from real Myntra files in `errors/myntra/`)

- **Identity = seller SKU code.** The Myntra MDirect Listings Report shows each style keyed by `seller sku code` (e.g. `127SDE826NSB`) = our Shopify SKU (= `van`). Myntra assigns its *own* `style id`/`sku id` on top; **our `styleGroupId` is only a per-sheet grouping requirement** ("Minimum unique StyleGroupIds required to process the sheet is 1"), not the listing key. So the meaningful duplicate question is "is this seller SKU already done?", not anything about `styleGroupId`.
- **Rejection files contain failed rows only.** All sampled per-row rejection sheets carry only `*_VALIDATION_FAILED` rows plus a `Resubmission Summary` sheet — Myntra never returns already-passed rows. So the Fix flow's `correct()` (which re-emits the file's rows) cannot duplicate passed rows *within* a file; the only cross-flow risk is fixed SKUs later being re-listed by Generate.

---

## Decisions (approved)

- **(a)** Change detection hashes the **final Myntra output row** (see §2).
- **(b)** Registry is recorded at **generate time** (not confirm) — the only thing that can guard the CLI path.
- **(c)** CLI uses a `--force` flag (non-interactive), **low priority**; the **web review screen is the primary deliverable**.
- Dedup source of truth is **the app's own registry**, not the Myntra report. (The report remains a useful corpus for future error-rule mining, out of scope here.)

---

## 1. The registry

New S3 JSON store, same pattern as `src/myntra/groupid_ledger.py`, in a new `src/myntra/sku_registry.py`.

- **Prod:** key `state/sku_registry.json` in the same bucket via `S3JsonStore`.
- **Dev:** separate local path via new env `SKU_REGISTRY_LOCAL_PATH` + `Settings.sku_registry_local_path` + an `sku_registry_store(settings)` helper (mirrors `ledger_store`/`hsn_store`). Needed because `LocalJsonStore` is one-file-per-path.

**Shape** — keyed by seller SKU:
```json
{
  "127SDE826NSB": {
    "content_hash": "a1b2c3…",
    "style_group_id": 41,
    "first_generated": "2026-07-01",
    "last_generated": "2026-07-03",
    "uploaded": true
  }
}
```

## 2. Change detection

`content_hash(mapped_row)` = stable hash (e.g. sha1 of a canonical JSON dump) of the mapped cells that reach the sheet — title, MRP, ISP, Product Details, Prominent Colour, fabric fields, HSN, image URLs, etc. — **excluding** `styleGroupId` (run-assigned and volatile). Same hash ⇒ UNCHANGED; different ⇒ CHANGED.

## 3. Partition (shared, testable)

`partition(products_mapped, registry)` classifies each incoming SKU:
- **NEW** — not in registry.
- **CHANGED** — in registry, `content_hash` differs.
- **UNCHANGED** — in registry, `content_hash` identical.

## 4. Generate flow behaviour

After mapping, partition, then:
- **NEW** → build; draw the next `styleGroupId` from the groupid ledger as today.
- **CHANGED** → build; **reuse the SKU's stored `style_group_id`** (an update, not a new style; avoids ledger churn — and Myntra keys on seller SKU anyway).
- **UNCHANGED** → do **not** build; surface for a "regenerate anyway?" decision (default No).

On build, **record/update** each built SKU in the registry: refresh `content_hash`, `last_generated`, set `style_group_id` (existing for CHANGED, new for NEW), and leave `uploaded` as-is here (it is flipped elsewhere — see §6).

**Surfaces:**
- **Web (primary):** the pre-build review screen lists UNCHANGED already-done SKUs with a "regenerate these anyway?" control; NEW/CHANGED are shown as auto-included. This reuses the **same pre-scan → review → build machinery as the HSN KB** — the two review steps merge into one pre-build screen, not two separate ones.
- **CLI (`run.py`, low priority):** prints a partition summary and **skips UNCHANGED by default**, building NEW+CHANGED; `--force` rebuilds everything. Non-interactive (no y/N prompt).

## 5. Images in S3 (no duplication)

S3 image keys are already deterministic — `<prefix>/<sku>/<n>.jpg` (`images.py`, `s3_upload.py`) — so a re-upload **overwrites the same key**, never creates a second copy. The registry improves on this: UNCHANGED SKUs are skipped entirely, so their images aren't downloaded, converted, or re-uploaded at all. Only NEW/CHANGED SKUs touch S3.

**Edge — shrinking image count:** if a CHANGED SKU drops from N to M<N images, old slots `<sku>/(M+1..N).jpg` remain as harmless leftovers (not duplicates). Add an optional cleanup that deletes `<sku>/*.jpg` beyond the current count for rebuilt SKUs, so S3 stays tidy.

## 6. Fix flow guard

`correct()` already emits only the rejection file's (failed) rows, so no in-file duplication. Addition: after a successful correction, **update the registry** for those SKUs (new `content_hash` from the corrected cells) so a later Generate treats them as already-done rather than NEW. `uploaded` is set true on "Mark upload successful" (existing confirm) and rolled back by the undo/`unconfirm` fix (see the HSN+fixes spec, Part B.2).

## 7. Where it lives

- New `src/myntra/sku_registry.py`: `content_hash(row)`, `partition(mapped, registry)`, `record(store, sku, hash, style_group_id, uploaded=None)`, `read_registry(store)`.
- Consumed by `src/myntra/pipeline.py` (CLI, with `--force`) and the web generate router (review screen).
- `map_product` already produces the cells to hash; no mapper change beyond passing rows to the registry.

## Testing

- `content_hash` stable across runs, ignores `styleGroupId`, changes when a sheet-bound field changes.
- `partition` → correct NEW/CHANGED/UNCHANGED buckets.
- CHANGED reuses stored `style_group_id`; NEW draws from ledger.
- Registry `record`/`read` round-trip (S3 mock + local store).
- Web: review screen lists UNCHANGED with regenerate-anyway; NEW/CHANGED auto-included; building records the registry.
- CLI: skips UNCHANGED by default; `--force` rebuilds all.
- Fix: correction updates the registry for fixed SKUs.
- Image cleanup: shrinking image count removes stale slots for a rebuilt SKU.

## Out of scope

- Ingesting the Myntra Listings Report as a live-state source (Gopal chose app-owned registry).
- Mining the `errors/myntra/` corpus to expand `error_rules.yaml` (worth doing later; separate effort).
- Interactive y/N prompt in the CLI (flag-only by decision (c)).
