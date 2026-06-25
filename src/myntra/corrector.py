from src.core.models import ImageResult, MappedRow
from src.myntra.fill import IMAGE_COLUMNS, fill_template

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
            else:
                plan["unknown"].append({"sku": re_.sku, "raw": issue["raw"]})
    return plan


def _image_result(sku, cells):
    urls = [cells[h] for h in _IMAGE_HEADERS if cells.get(h)]
    return ImageResult(sku=sku, passed_urls=urls)


def correct(row_errors, template, template_path, constants, answers, drops, out_path):
    """Apply drops + user answers + deterministic auto-fixes, regenerate a sheet.
    answers = {sku: {field: value}}; drops = set(sku). Returns a summary dict."""
    rows = []
    summary = {"written": 0, "dropped": [], "changed": {}}
    for re_ in row_errors:
        if re_.sku in drops:
            summary["dropped"].append(re_.sku)
            continue
        cells = dict(re_.cells)
        changed = []
        # deterministic auto-fixes derived from issue categories
        for issue in re_.issues:
            if issue["category"] == "pincode":
                for h in ("Manufacturer Name and Address with Pincode",
                          "Packer Name and Address with Pincode"):
                    if constants.get(h):
                        cells[h] = constants[h]
                        changed.append(h)
        # user answers (manual choices), e.g. Prominent Colour
        for field, value in (answers.get(re_.sku) or {}).items():
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
