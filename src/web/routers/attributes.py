import os

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse

from src.core.shopify_reader import read_products
from src.myntra.attribute_entry import (BRAND_COLOUR_HEADER, AttributeValueError,
                                        SkuMismatchError, attribute_vocab,
                                        derive_brand_colour, user_filled_attributes,
                                        validate_submitted, write_attributes)
from src.myntra.pipeline import DEFAULT_TEMPLATE_NAME
from src.myntra.preview import build_card, is_set, read_filled_rows
from src.myntra.template_reader import read_template
from src.web.jobs import store
from src.web.routers.pages import get_user

router = APIRouter()
TEMPLATE = os.path.join("templates", "myntra", DEFAULT_TEMPLATE_NAME)
EXPIRED = "session expired, please re-upload"


def _templates():
    from src.web.main import templates
    return templates


def job_files(job_id):
    """(job, job_dir, xlsx_path, csv_path) or 404 if the job/build is gone.
    RUNTIME is read from the generate router at call time so tests can point it
    at a tmp dir."""
    from src.web.routers.generate import RUNTIME, _safe_job_id
    job_id = _safe_job_id(job_id)
    job = store.get(job_id)
    if not job or not job.result:
        raise HTTPException(status_code=404, detail=EXPIRED)
    job_dir = os.path.join(RUNTIME, job_id)
    xlsx = job.result.get("filled")
    if not xlsx or not os.path.exists(xlsx):
        raise HTTPException(status_code=404, detail=EXPIRED)
    return job, job_dir, xlsx, os.path.join(job_dir, "products_export.csv")


def _panels(xlsx, csv_path, template, columns):
    products = {}
    if os.path.exists(csv_path):
        products = {p.sku: p for p in read_products(csv_path)}
    panels = []
    for ordinal, attrs in enumerate(read_filled_rows(xlsx, template)):
        sku = attrs.get("vendorSkuCode") or ""
        p = products.get(sku)
        panels.append({
            "ordinal": ordinal,
            "sku": sku,
            "product_title": p.title if p else "",
            "image": (p.images[0] if p and p.images else None),
            # NOT "values": in Jinja `p.values` would resolve to dict.values (the
            # method), silently breaking the pre-selection comparison.
            "chosen": {c: attrs.get(c) for c in columns},
            "filled": sum(1 for c in columns if is_set(attrs.get(c))),
            # Shown read-only: what is in the sheet now, not a guess at what a
            # pending selection would produce.
            "brand_colour": attrs.get(BRAND_COLOUR_HEADER),
            "card": build_card(attrs, columns),
        })
    return panels


@router.get("/generate/attributes/{job_id}", response_class=HTMLResponse)
def attributes_form(request: Request, job_id: str):
    user = get_user(request)
    job, _job_dir, xlsx, csv_path = job_files(job_id)
    template = read_template(TEMPLATE)
    columns = user_filled_attributes()
    return _templates().TemplateResponse(request, "attributes.html", {
        "user": user, "job_id": job.id, "columns": columns,
        "vocab": attribute_vocab(template, columns),
        "panels": _panels(xlsx, csv_path, template, columns),
        "total": len(columns)})


def _submitted(form, columns):
    """Parse attr__{ordinal}__{column_index} + sku__{ordinal} into
    {ordinal: {"sku": str, "values": {column: raw_value}}}, in ordinal order."""
    entries = {}
    for key, value in form.items():
        if key.startswith("sku__"):
            ordinal = int(key.split("__")[1])
            entries.setdefault(ordinal, {"sku": "", "values": {}})["sku"] = str(value)
        elif key.startswith("attr__"):
            _, ordinal, col_index = key.split("__")
            ordinal, col_index = int(ordinal), int(col_index)
            if 0 <= col_index < len(columns):
                entry = entries.setdefault(ordinal, {"sku": "", "values": {}})
                entry["values"][columns[col_index]] = str(value)
    return dict(sorted(entries.items()))


@router.post("/generate/attributes/{job_id}/preview", response_class=HTMLResponse)
async def attributes_live_preview(request: Request, job_id: str):
    get_user(request)
    job_files(job_id)                      # 404s an expired job before doing work
    columns = user_filled_attributes()
    entries = _submitted(await request.form(), columns)
    _ordinal, entry = next(iter(entries.items()), (0, {"sku": "", "values": {}}))
    attrs = dict(entry["values"])
    attrs["vendorSkuCode"] = entry["sku"]
    return _templates().TemplateResponse(
        request, "_preview_card.html", {"c": build_card(attrs, columns)})


@router.post("/generate/attributes/{job_id}", response_class=HTMLResponse)
async def attributes_save(request: Request, job_id: str):
    get_user(request)
    job, _job_dir, xlsx, _csv = job_files(job_id)
    template = read_template(TEMPLATE)
    columns = user_filled_attributes()
    vocab = attribute_vocab(template, columns)
    entries = _submitted(await request.form(), columns)

    has_brand_colour = BRAND_COLOUR_HEADER in template.col_index_by_header
    try:
        payload = []
        for ordinal, e in entries.items():
            values = validate_submitted(e["values"], vocab)
            # Derived, never typed — Myntra rejects a null Brand Colour (Remarks).
            if has_brand_colour:
                values[BRAND_COLOUR_HEADER] = derive_brand_colour(values)
            payload.append({"ordinal": ordinal, "sku": e["sku"], "values": values})
        saved = write_attributes(xlsx, template, payload)
    except (AttributeValueError, SkuMismatchError) as exc:
        return _templates().TemplateResponse(
            request, "_attr_saved.html", {"job_id": job.id, "error": str(exc)})
    return _templates().TemplateResponse(
        request, "_attr_saved.html", {"job_id": job.id, "saved": saved})
