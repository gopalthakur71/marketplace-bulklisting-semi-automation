# Myntra Bulk-Listing Automation — Phase 1

Deterministic fill: Shopify saree CSV + Myntra DIY template -> filled Sarees sheet + JPG images.

## Run
1. Put `products_export.csv` and `Myntra-Sku-Template-2026-06-16.xlsx` in `input/` (or repo root).
2. `pip install -r requirements.txt`
3. `python run.py`

Outputs to `output/`: `myntra_filled.xlsx`, `images/SKU_n.jpg`, `report.txt`.

## Notes
- No LLM/API/DB (that is Phase 2).
- Attribute fields Shopify lacks are left blank and listed in `report.txt`.
- Dropdown-validation preservation in the output file is a deferred decision.

## Test
`python -m pytest -v`
