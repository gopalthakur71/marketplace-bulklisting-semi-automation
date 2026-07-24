"""Reconstruct Myntra's auto-generated title/design-details from a filled row's
attributes (approximate, for the in-app preview) and read a filled workbook.

Myntra generates the customer-facing title and 'Design Details' prose from the
attribute columns; the exact wording is not something we can guarantee, so these
reconstructions are labelled approximate in the UI. Specifications shown alongside
are exact (they are the values the user entered)."""
import warnings

import openpyxl

SHEET_SAREES_NAME = "Sarees"

# Title order, reverse-engineered from live IJOR listings (2026-07-24). Colour is
# NOT in the saree title. Only attributes that are set are included.
_TITLE_ORDER = ["Print or Pattern Type", "Ornamentation", "Saree Fabric", "Type"]


def is_set(value):
    """True unless the value is None, blank, or the literal 'NA' (case-insensitive)."""
    if value is None:
        return False
    s = str(value).strip()
    return s != "" and s.upper() != "NA"


def reconstruct_title(attrs):
    parts = [str(attrs.get(h)).strip() for h in _TITLE_ORDER if is_set(attrs.get(h))]
    parts.append("Saree")
    title = " ".join(parts)
    if is_set(attrs.get("Blouse Fabric")):
        title += " With Unstitched Blouse Piece"
    return title


def reconstruct_design_details(attrs):
    lines = []
    colour, typ = attrs.get("Prominent Colour"), attrs.get("Type")
    if is_set(colour):
        l1 = str(colour).strip()
        if is_set(typ):
            l1 += " " + str(typ).strip()
        lines.append(l1 + " sarees")
    pattern, border = attrs.get("Pattern"), attrs.get("Border")
    if is_set(pattern) or is_set(border):
        p = str(pattern).strip() if is_set(pattern) else ""
        b = str(border).strip() if is_set(border) else ""
        lines.append(f"{p} saree with {b} Border".replace("  ", " ").strip())
    orn = attrs.get("Ornamentation")
    if is_set(orn):
        lines.append(f"Has {str(orn).strip()} detail")
    return lines


def missing_attributes(attrs, user_filled):
    return [h for h in user_filled if not is_set(attrs.get(h))]


def read_filled_rows(xlsx_path, template):
    """One {header: value_or_None} dict per data row that carries a vendorSkuCode."""
    warnings.filterwarnings("ignore")
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb[SHEET_SAREES_NAME]
    sku_col = template.col_index_by_header.get("vendorSkuCode")
    rows = []
    for r in range(template.first_data_row, ws.max_row + 1):
        if sku_col is None or not is_set(ws.cell(row=r, column=sku_col).value):
            continue
        cells = {}
        for header, col in template.col_index_by_header.items():
            v = ws.cell(row=r, column=col).value
            cells[header] = None if v is None else str(v).strip()
        rows.append(cells)
    wb.close()
    return rows
