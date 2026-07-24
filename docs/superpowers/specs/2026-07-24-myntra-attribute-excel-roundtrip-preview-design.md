# Spec: Myntra Attributes — User-Decided in Excel + Listing Preview

**Project:** Myntra bulk-listing pipeline (Shopify CSV → Myntra template, sarees only)
**Type:** Behaviour change to the fill pipeline + one new preview surface
**Date:** 2026-07-24
**Branch:** `feat/attribute-mapping-vocab`
**Supersedes the build plan in:** `docs/journal/myntra-attribute-mapping-fix-spec.md` (that spec assumed
deterministic auto-extraction + a self-learning synonym map; both are explicitly dropped here — see §2).

---

## 1. Problem

Myntra ignores the product name/description we submit and **auto-generates the visible title and the
"Design Details" prose from the attribute columns**. So the attributes *are* the listing. Today the
pipeline mishandles the attributes that drive the title:

- `Type` is the constant `NA`, `Border` and `Pattern` are the constant `Solid`, `Ornamentation` is never
  set, and `Print or Pattern Type` is never set — regardless of the actual product.
- `Prominent Colour` is auto-extracted and `Saree/Blouse Fabric` are auto-filled from a 2-keyword
  (cotton/silk) block. Both are guesses that can be wrong, and a wrong attribute becomes a wrong public
  title.

The owner's decision (2026-07-24): **the machine must not guess these.** Fabric/attribute semantics
("silk" that is really a blend; a "solid border" vs "no border") are a human judgment made better by a
person than by any auto-matcher or self-learning app. The owner accepts a little extra per-batch effort
as the cost of listing quality.

## 2. Goals / Non-goals

**Goals**
1. The name-driving attributes become **user-decided**, chosen from Myntra's own dropdowns **in Excel**,
   using the owner's existing "fill the template by hand, then upload" workflow.
2. After filling, the owner can **preview a Myntra-style listing** per product in the app before
   uploading to Myntra, to catch mistakes.

**Non-goals (explicitly dropped)**
- ❌ No deterministic auto-extraction of the user-decided attributes (no scanning tags/title to fill them).
- ❌ No self-learning / synonym map / "nearest value" memory. Zero machine matching.
- ❌ No web data-entry grid. Data entry happens in Excel.
- ❌ No attempt to pixel-match Myntra's exact generated title/description wording.
- ❌ No change to brand, HSN, pricing, images, styleGroupId, identity columns, or Product Details logic.

## 3. Feature A — Attributes user-decided via Excel dropdowns

### 3.1 Columns left blank-with-dropdown (user fills in Excel)

Eight name-driving columns plus Wash Care (9 total):

`Prominent Colour` · `Saree Fabric` · `Blouse Fabric` · `Type` · `Ornamentation` · `Border` ·
`Pattern` · `Print or Pattern Type` · `Wash Care`

`Wash Care` is included because it was previously *derived from fabric*; since the user now chooses
fabric, that derivation is gone, so Wash Care becomes a user dropdown too (only 3 accepted values).
`materialCareDescription` remains a constant.

### 3.2 What the pipeline stops doing

- **Colour:** remove the `prominent_colour_from_name` extraction path (mapper step 6) and its
  `Brand Colour (Remarks)` side-write. Leave `Prominent Colour` blank.
- **Fabric:** the `fabric_detection` block no longer writes `Saree Fabric` / `Blouse Fabric` / `Wash Care`.
  Leave those three blank. **Do NOT remove `fabric_detection` itself** — its `order` list (cotton, silk) is
  still consumed by `hsn_kb.signature()` to derive the HSN signature from the title when the Shopify
  fabric metafield is blank (`mapper.py` step 5b). Only the attribute-header writes (step 5) are removed;
  the fabric-keyword detection that feeds HSN stays untouched.
- **Constants:** remove `Type`, `Border`, `Pattern` from `config/myntra/constants.yaml` so they are no
  longer force-written as `NA`/`Solid`.
- `Ornamentation` and `Print or Pattern Type` are already unset — keep them blank.

### 3.3 No pre-fill

All nine columns are left **fully blank**. Even when a value is literally present in the Shopify tags
(e.g. `Banarasi`), the app does **not** pre-fill it — consistent with "the human decides," and it keeps
the code near-zero. (Rejected alternative: pre-fill only exact literal vocab hits. Rejected because it
reintroduces extraction logic the owner steered away from.)

### 3.4 The dropdown mechanism (template switch)

Switch the pipeline template from `templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx` to
`templates/myntra/Myntra-Sku-Template-2026-07-24.xlsx` (V13).

- The old (06-16) template stores dropdowns as **x14** extension validations, which openpyxl **drops**
  on save — which is why current outputs have no dropdowns.
- V13 stores dropdowns as **11,100 plain `<dataValidation type="list">`** entries referencing the hidden
  `masterdata` sheet, which openpyxl **preserves** through load→save (verified: all 11,100 survive a 3.7s
  round-trip; `shared_string→inline` conversion does not touch them).

So the file the app produces already carries live dropdowns on the blank columns. The owner opens it in
Excel, picks values from the dropdowns, saves, and uploads to Myntra. Because the **owner re-saves through
Excel**, the uploaded file is Excel-authored — the same kind of file the owner already fills by hand and
uploads successfully — which is what avoids the historical "POI rejects re-injected validation XML" issue.

### 3.5 Template-compatibility guard

Before/at switch, verify that **every** Myntra header written by `config/myntra/column_map.yaml` and
`config/myntra/constants.yaml` (and the derived/identity writes in `mapper.py`) exists in V13's `Sarees`
header row. Leading headers already match between the two templates; the guard must fail loudly (raise,
not silently skip) if any expected header is missing, so a template swap can never silently drop a column.

## 4. Feature B — Myntra listing preview (round-trip re-upload)

### 4.1 Flow

1. Generate → owner downloads the fill-in file (9 blank columns + dropdowns).
2. Owner fills the attributes in Excel, saves.
3. In the app: **"Preview listings"** → owner uploads the filled file back.
4. App reads each `Sarees` data row and renders one **Myntra-style preview card per product**.
5. If satisfied, the owner uploads the **same Excel file** to Myntra. The app never modifies the file —
   preview is strictly read-only.

Chosen because it is the cleanest with the least code (no live web form, no file rewriting).

### 4.2 Preview card — two zones with different reliability

**① Specifications — EXACT.** The attribute values the owner entered, shown as a Myntra-style spec table
(Colour, Saree Fabric, Blouse Fabric, Type, Ornamentation, Border, Pattern, Print/Pattern, Wash Care, and
any other spec columns already in the row). 100% reliable — it is literally the entered values.

**② Title & Design Details — APPROXIMATE, badged.** Reconstructed from the formula reverse-engineered
from four live IJOR listings (2026-07-24):

- **Title** ≈ `[Print or Pattern Type] [Ornamentation] [Saree Fabric] [Type] "Saree"`
  `[+ " With Unstitched Blouse Piece"]` — only attributes that are set; **colour is NOT in the title**.
- **Design Details** =
  - L1: `"{Prominent Colour} [{Type}] sarees"`
  - L2: `"{Pattern} saree with {Border} Border"`
  - L3 (only if Ornamentation set): `"Has {Ornamentation} detail"`

Both zone-② strings carry a visible badge: **"Myntra generates this automatically — this is our best
reconstruction, not guaranteed word-for-word."** We deliberately do **not** try to pixel-match Myntra.

> Note: the live "Border border" doubling seen on Myntra is a Myntra rendering bug, not ours — the Border
> dropdown values are clean single words. The reconstruction appends " Border" once; we neither reproduce
> nor try to predict Myntra's doubling.

### 4.3 Missing-attribute check (bonus)

While rendering, if any of the nine user-filled columns is blank for a row, the card flags it
(e.g. "Type not filled") so the owner catches misses before uploading to Myntra.

## 5. Safety / correctness

- **Template-compatibility guard** (§3.5) — hard failure if V13 is missing an expected header.
- **Dropdown-preservation test** — an output built from V13 retains its `Sarees` data-validations and
  leaves the nine columns blank.
- **`shared_string→inline` non-interference** — a test confirms that step leaves validations intact.
- **One real Myntra upload (owner-run)** — the plan's final manual gate: the owner generates one batch,
  fills it, and uploads a single test file to Myntra to confirm acceptance. (Automated code cannot upload
  to Myntra; this is the honest end check. Low risk — it mirrors the proven manual workflow.)

## 6. Testing

- mapper no longer emits any of the nine attributes: colour not extracted, fabric not filled, no
  `NA`/`Solid` constants written. (Update existing mapper tests accordingly.)
- V13-built output: dropdowns retained, nine columns blank (new test).
- template-compatibility guard test (passes for V13; fails on a synthetic template missing a header).
- preview reconstruction: title + L1/L2/L3 assembled correctly from a sample row, including the
  "only if set" rules and the blouse-piece suffix; missing-attribute flagging.
- preview is read-only: the uploaded file is byte-for-byte unchanged after preview.
- existing pipeline / e2e tests still green.

## 7. Open items / risks

- **Full header-set parity** between 06-16 and V13 is assumed from matching leading headers but must be
  verified across all written headers (the guard makes this safe).
- **Myntra acceptance of the V13-derived, Excel-re-saved file** is confirmed only by the owner's real
  upload test (§5). If Myntra rejects it, fall back options: (a) keep filling the file the owner already
  uses by hand, or (b) revisit whether any app-side XML step needs adjusting for V13.
- Preview parsing must tolerate the template's header row / first-data-row offsets (reuse
  `template_reader` conventions rather than hard-coding row numbers).
