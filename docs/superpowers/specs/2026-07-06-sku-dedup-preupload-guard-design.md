# Pre-Upload Duplicate-Generation Guard (SKU Registry) — Design

**Date:** 2026-07-06
**Status:** Approved (verbal), building immediately
**Branch:** `feat/hsn-knowledge-base` (same batch as HSN KB + backlog fixes)
**Supersedes for the pre-upload case:** `docs/superpowers/specs/2026-07-03-sku-dedup-guard-design.md` (the richer post-Myntra partition stays that spec's concern; see Out of scope).

---

## Why

Re-uploading an already-generated `products_export.csv` silently regenerates the sheet and reserves fresh `styleGroupId`s, with no warning. Gopal hit this **before** ever uploading to Myntra: he generated a sheet, didn't upload it, re-ran the same export, and the app just built it again. The app should recognise "these SKUs are already generated," stop, and offer the already-generated file — not quietly rebuild.

This is the **pre-Myntra** duplicate case. The post-Myntra flow (edited SKUs, "regenerate anyway", the `uploaded` flag) is deliberately left to the 2026-07-03 spec.

## HSN model clarification (drives the storage shape)

`category|fabric` signature → HSN is **only a suggestion**, never authoritative and never auto-applied. The **user always decides** which HSN a product gets; the app only offers chips (the signature KB from Part A, unchanged). Therefore the authoritative HSN record is **per-SKU**, pinned in the registry. Same SKU ⇒ same HSN forever, so a rebuild is byte-for-byte identical.

## Decisions (approved)

- **(a)** Source of truth is the app's own registry, keyed by **seller SKU** (= Shopify SKU = `van`). Not the Myntra report.
- **(b)** Recorded at **generate time** (not upload-to-Myntra) — the only thing that catches this pre-upload repeat.
- **(c)** Duplicate response is **warn + offer download**, not silent rebuild. Warn when **any** uploaded SKU is a repeat ("warn on any repeat").
- **(d)** The download is produced by **rebuild-on-demand**: re-run the pipeline on the just-uploaded CSV, forcing each SKU's **pinned styleGroupId and pinned HSN** from the registry. Deterministic ⇒ identical file. No extra S3 object stored, no ledger change. (Minimal records now; a DB comes only if this becomes SaaS.)
- **(e)** `content_hash` **excludes** `styleGroupId` and `HSN`, so the duplicate check runs at upload time, *before* the HSN review.

---

## 1. The registry

New JSON store, same pattern as `src/myntra/groupid_ledger.py` / `src/myntra/hsn_kb.py`, in a new `src/myntra/sku_registry.py`.

- **Prod:** key `state/sku_registry.json` in the same bucket via `S3JsonStore`.
- **Dev:** separate local path via new env `SKU_REGISTRY_LOCAL_PATH` + `Settings.sku_registry_local_path` + an `sku_registry_store(settings)` helper (mirrors `ledger_store` / `hsn_store`; needed because `LocalJsonStore` is one-file-per-path).

**Shape — keyed by seller SKU:**
```json
{
  "169SDE326SFSF": {
    "content_hash": "a1b2c3…",
    "style_group_id": 13,
    "hsn": "50072010",
    "first_generated": "2026-07-06",
    "last_generated": "2026-07-06"
  }
}
```

**Public functions:**
- `content_hash(cells) -> str` — stable sha1 of a canonical JSON dump of the sheet-bound cells, **excluding `styleGroupId` and `HSN`**.
- `read_registry(store) -> dict` — `{}` when absent.
- `partition(sku_hashes, registry) -> {"new": [...], "repeat": [...], "edited": [...]}` — per SKU: **NEW** (not in registry), **REPEAT** (in registry, hash matches), **EDITED** (in registry, hash differs). `sku_hashes` is a list of `(sku, content_hash)`.
- `record(store, sku, content_hash, style_group_id, hsn) -> None` — upsert: set/refresh `content_hash`, `style_group_id`, `hsn`, `last_generated`; set `first_generated` once.

## 2. Change detection

`content_hash(cells)` hashes the mapped cells that reach the sheet (title, MRP, ISP, Product Details, Prominent Colour, fabric fields, image-independent attributes, …) **minus `styleGroupId` and `HSN`**. Fabric is included, and HSN only ever derives from a user choice, so excluding HSN never masks a real content change. Same hash ⇒ REPEAT; different ⇒ EDITED.

To compute this at upload (before images/HSN), the guard maps products with `map_product(..., hsn_by_signature=None)` (HSN unset) and no image step, then hashes `mapped.cells`.

## 3. Generate flow

On `POST /generate` (after saving the CSV, before the HSN review):

1. Map each product (no images, no HSN), compute `content_hash` per SKU.
2. `partition` against the registry.
3. **No repeats** → unchanged existing flow: HSN pre-scan → review → build. **On build, `record()` each SKU** with its assigned `style_group_id` and chosen `hsn` (from `hsn_by_signature[signature]`) and `content_hash`.
4. **Any repeat** → render a **duplicate-warning screen** (before the HSN step):
   > **⚠ You are uploading SKUs that were already generated.** This file has already been generated. If you got an error from Myntra, upload the error file on the **Fix errors** page. *(then lists the repeat SKUs)*
   - **[⬇ Download the already-generated sheet]** → `POST /generate/rebuild/{job_id}` → rebuild-on-demand and stream the `.xlsx`.
   - **[Generate the N new SKUs only]** (shown only when `new`+`edited` is non-empty) → proceed to the normal HSN review/build for just those SKUs.

Persist the partition + csv_path to the job dir (`dedup.json`), mirroring how `hsn.json` / `rows.json` are persisted, so the rebuild and "new only" routes can resume.

## 4. Rebuild-on-demand

`POST /generate/rebuild/{job_id}`: read `dedup.json`, then run `pipeline.main` on the stored `csv_path` with two per-SKU overrides sourced from the registry:
- `style_group_id_by_sku` — force each SKU's pinned `styleGroupId` (bypasses the ledger entirely; nothing is reserved or advanced).
- `hsn_by_sku` — force each SKU's pinned HSN (bypasses the KB suggestion path).

`pipeline.main` gains optional `style_group_id_by_sku` and `hsn_by_sku` maps. When present they take precedence over the sequential ledger id and the `hsn_by_signature` lookup respectively. Images re-process/overwrite the same deterministic S3 keys (no duplication). The result streams straight to the browser (`FileResponse`), same as the normal download.

## 5. "Generate new SKUs only"

The "generate the new ones" action filters the CSV/products to the `new`+`edited` SKUs and runs the standard flow (HSN review → build → record) for just those. EDITED SKUs are treated like NEW here (fresh id, freshly asked HSN); reusing ids for edits is the post-Myntra spec's job.

## 6. Where it plugs in

- New `src/myntra/sku_registry.py` (`content_hash`, `read_registry`, `partition`, `record`).
- `src/web/settings.py`: `Settings.sku_registry_local_path`, `SKU_REGISTRY_LOCAL_PATH`, `sku_registry_store()`.
- `src/myntra/pipeline.py`: optional `style_group_id_by_sku` / `hsn_by_sku` overrides threaded into the loop and `map_product`.
- `src/web/routers/generate.py`: partition on upload; new `_dedup_warn.html`; routes `POST /generate/rebuild/{job_id}` and the "new only" branch; `record()` on every build (normal + new-only).
- New template `src/web/templates/_dedup_warn.html`.

## Testing

- `content_hash`: stable across runs; ignores `styleGroupId` and `HSN`; changes when a sheet-bound field changes.
- `partition`: correct NEW / REPEAT / EDITED buckets.
- `record`/`read_registry`: round-trip on the local store; `first_generated` set once, `last_generated` refreshed; HSN + styleGroupId pinned.
- Pipeline: `style_group_id_by_sku` overrides the sequential id; `hsn_by_sku` overrides the signature lookup; a full rebuild reproduces the same styleGroupId + HSN cells for a known SKU.
- Web: first generate records the registry; re-upload the same CSV → warning screen listing the repeat SKUs, no HSN review, ledger unchanged; rebuild route streams an `.xlsx` with the pinned id/HSN; mixed file offers "generate new only" and building it records only the new SKUs.

## Out of scope

- Post-Myntra partition: reusing ids for EDITED SKUs, "regenerate anyway", the `uploaded` flag, Fix-flow registry updates (the 2026-07-03 spec).
- Storing the generated `.xlsx` durably in S3 (rejected in favour of deterministic rebuild).
- CLI (`run.py`) guard / `--force` (low priority; web is the deliverable).
- Ingesting the Myntra Listings Report; mining `errors/myntra/`.
