import datetime
from collections import OrderedDict

from src.core.models import ImageResult, MappedRow
from src.myntra.fill import IMAGE_COLUMNS, fill_template
from src.myntra.mapper import validate_value
from src.myntra.error_reader import RowError
from src.myntra.correction_log import append as log_append
from src.myntra.signature import normalize
from src.myntra.pipeline import main as pipeline_main
from src.myntra.sku_registry import read_registry
from src.web.settings import sku_registry_store

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


def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _derive_changes(sku, cells_before, answers, constants, changed_fields):
    """Best-effort {field: [old, new]} for the correction log. The log is a
    Phase-D breadcrumb (not read in this build), so approximating `old` from the
    pre-fix cells and `new` from the deterministic source is acceptable."""
    changes = {}
    for field in changed_fields:
        old = cells_before.get(field, "") or ""
        if field in (answers.get(sku) or {}):
            new = answers[sku][field]
        elif field == "ISP":
            new = cells_before.get("MRP", "")
        else:
            new = constants.get(field, "")
        changes[field] = [old, new]
    return changes


def correct_from_issues(issues, template, template_path, constants, answers, out_path,
                        log_store=None, fix_id=None):
    """Surface A: correct SKUs in place from ExplainedIssue records. A SKU with any
    explain_only issue is excluded from the file (reported under 'manual_needed');
    drop_sku SKUs are dropped; the rest go through the deterministic correct()."""
    by_sku = OrderedDict()
    for it in issues:
        by_sku.setdefault(it.sku, []).append(it)

    rows, drops, manual_needed = [], set(), []
    cells_before = {}
    for sku, its in by_sku.items():
        if any(i.action == "explain_only" for i in its):
            manual_needed.append({
                "sku": sku,
                "explanation": "; ".join(i.explanation for i in its
                                         if i.action == "explain_only")})
            continue
        cells = {}
        for it in its:
            if it.cells:
                cells.update(it.cells)
        cells_before[sku] = dict(cells)
        rows.append(RowError(
            row=0, sku=sku, status="", cells=cells,
            issues=[{"category": i.category, "action": i.action, "field": i.field,
                     "explanation": i.explanation, "raw": i.raw_reason} for i in its]))
        if any(i.action == "drop_sku" for i in its):
            drops.add(sku)

    summary = correct(rows, template, template_path, constants, answers, drops, out_path)
    summary["manual_needed"] = manual_needed

    if log_store is not None:
        for sku, fields in summary.get("changed", {}).items():
            first_raw = next((i.raw_reason for i in by_sku.get(sku, [])), "")
            log_append(log_store, {
                "timestamp": _now_iso(),
                "fix_id": fix_id,
                "sku": sku,
                "signature": normalize(first_raw)[0] if first_raw else "",
                "changes": _derive_changes(sku, cells_before.get(sku, {}),
                                           answers, constants, fields)})
    return summary


def regenerate_surface_b(skus, settings, out_dir, csv_path=None):
    """Surface B / A': rebuild rejected SKUs from the SKU registry pins + the
    Shopify export, applying the CURRENT constants.yaml (so brand/address/pincode
    fixes flow through). skus=None rebuilds the whole sheet. SKUs resolvable in
    neither the registry nor the export are reported as could_not_rebuild."""
    reg = read_registry(sku_registry_store(settings))
    only = set(skus) if skus else None
    sgid, hsn = {}, {}
    for sku in (skus or []):
        e = reg.get(sku)
        if not e:
            continue
        if e.get("style_group_id") is not None:
            sgid[sku] = e["style_group_id"]
        if e.get("hsn") is not None:
            hsn[sku] = e["hsn"]

    res = pipeline_main(csv_path=csv_path, out_dir=out_dir, only_skus=only,
                        style_group_id_by_sku=sgid, hsn_by_sku=hsn)

    built = {r["sku"] for r in res.get("records", [])}
    missing = sorted(set(skus) - built) if skus else []
    return {
        "written": res.get("products", 0),
        "file": res.get("filled"),
        "fixed": sorted(built),
        "could_not_rebuild": missing,
        "manual_needed": [],
        "dropped": [],
        "rejected": {},
        "changed": {},
    }
