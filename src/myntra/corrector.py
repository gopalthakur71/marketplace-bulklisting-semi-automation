from src.core.models import ImageResult, MappedRow
from src.myntra.fill import IMAGE_COLUMNS, fill_template
from src.myntra.mapper import validate_value

# image column order is the same list the sheet-writer uses
_IMAGE_HEADERS = IMAGE_COLUMNS


def plan_corrections(row_errors):
    """Summarise what will happen per SKU before the user confirms."""
    plan = {"auto": [], "drop": [], "manual": [], "unknown": []}
    for re_ in row_errors:
        for issue in re_.issues:
            act = issue["action"]
            if act == "drop_sku":
                if re_.sku not in plan["drop"]:
                    plan["drop"].append(re_.sku)
            elif act == "manual_choice":
                plan["manual"].append({"sku": re_.sku, "field": issue.get("field"),
                                       "explanation": issue["explanation"], "choices": []})
            elif act == "auto_fix":
                if re_.sku not in plan["auto"]:
                    plan["auto"].append(re_.sku)
            else:  # explain_only (known wording with guidance, or unrecognised)
                plan["unknown"].append({"sku": re_.sku, "raw": issue["raw"],
                                        "explanation": issue["explanation"]})
    return plan


def _image_result(sku, cells):
    urls = [cells[h] for h in _IMAGE_HEADERS if cells.get(h)]
    return ImageResult(sku=sku, passed_urls=urls)


def correct(row_errors, template, template_path, constants, answers, drops, out_path):
    """Apply drops + user answers + deterministic auto-fixes, regenerate a sheet.
    answers = {sku: {field: value}}; drops = set(sku). Returns a summary dict."""
    rows = []
    summary = {"written": 0, "dropped": [], "changed": {}, "rejected": {}}
    for re_ in row_errors:
        if re_.sku in drops:
            summary["dropped"].append(re_.sku)
            continue
        cells = dict(re_.cells)
        changed = []
        # deterministic auto-fixes derived from issue categories
        for issue in re_.issues:
            if issue["category"] in ("pincode", "address"):
                for h in ("Manufacturer Name and Address with Pincode",
                          "Packer Name and Address with Pincode"):
                    if constants.get(h):
                        cells[h] = constants[h]
                        changed.append(h)
            elif issue["category"] == "brand":
                if constants.get("brand"):
                    cells["brand"] = constants["brand"]
                    changed.append("brand")
            elif issue["category"] == "numeric":
                # Backfill an empty selling price (ISP) from MRP. fill_template
                # coerces MRP/ISP to real numbers, which covers the "non numeric"
                # half of this category for values that are already present.
                if not cells.get("ISP") and cells.get("MRP"):
                    cells["ISP"] = cells["MRP"]
                    changed.append("ISP")
        # user answers (manual choices), e.g. Prominent Colour
        for field, value in (answers.get(re_.sku) or {}).items():
            # For dropdown-controlled fields, the answer must be a real Myntra
            # vocab value. Canonicalize to the template's exact spelling; if it
            # isn't a valid option, don't write it — report it for re-prompting.
            if field in template.vocab_by_header:
                canon = validate_value(value, template.vocab_by_header[field])
                if canon is None:
                    summary["rejected"].setdefault(re_.sku, []).append(
                        {"field": field, "value": value})
                    continue
                value = canon
            cells[field] = value
            changed.append(field)
            # mirror the colour into the free-text Brand Colour (Remarks)
            if field == "Prominent Colour" and "Brand Colour (Remarks)" in template.col_index_by_header:
                cells["Brand Colour (Remarks)"] = str(value).lower()
        rows.append((MappedRow(sku=re_.sku, cells=cells), _image_result(re_.sku, cells)))
        if changed:
            summary["changed"][re_.sku] = changed
    fill_template(template_path, template, rows, out_path)
    summary["written"] = len(rows)
    return summary
