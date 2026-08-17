import os
import threading

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse

from src.core.shopify_reader import read_products
from src.myntra.attribute_entry import (BRAND_COLOUR_HEADER, HSN_HEADER,
                                        PINNED_NAME_HEADERS,
                                        AttributeValueError,
                                        SkuMismatchError, attribute_vocab,
                                        derive_brand_colour, freetext_hints,
                                        user_filled_attributes,
                                        user_filled_freetext, validate_freetext,
                                        validate_hsn, validate_submitted,
                                        write_attributes)
from src.myntra.fill import IMAGE_COLUMNS
from src.myntra.hsn_source import normalize as normalize_hsn
from src.myntra.pipeline import DEFAULT_TEMPLATE_NAME
from src.myntra.preview import build_card, is_set, read_filled_rows
from src.myntra.sku_registry import update_hsn, update_names
from src.myntra.template_reader import read_template
from src.web.jobs import store
from src.web.routers.pages import get_settings, get_user
from src.web.settings import sku_registry_store

router = APIRouter()
TEMPLATE = os.path.join("templates", "myntra", DEFAULT_TEMPLATE_NAME)
EXPIRED = "session expired, please re-upload"

# Every save is a read-modify-write of the whole workbook (openpyxl load -> save ->
# shared_to_inline). Two panels saved at the same moment would interleave and the
# second write would silently drop the first one's values, with no error shown.
# Serialising all saves costs a single owner nothing.
_WRITE_LOCK = threading.Lock()


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


def _filled_count(values, columns, free_columns):
    """The one definition of "N filled". Shared by the screen and the per-panel
    save so a future column change cannot make the two disagree."""
    return sum(1 for c in list(columns) + list(free_columns) + [HSN_HEADER]
               if is_set(values.get(c)))


def _total(columns, free_columns):
    """12 dropdowns + the free-text columns + HSN. One definition for the same
    reason as _filled_count: the screen and the per-panel save must agree."""
    return len(columns) + len(free_columns) + 1


def _hsn_gaps_in(panels):
    """SKUs with no usable HSN, counted off panels already built. Blank and
    malformed count alike — Myntra rejects both, so the screen must not
    distinguish them."""
    return sum(1 for p in panels if p["hsn_missing"])


def _hsn_gap_count(xlsx, template):
    """The same count read fresh from the sheet, for the save path — which needs
    post-write state and has no panels in hand. The screen must NOT use this: it
    would reload the whole workbook to recount rows _panels just read."""
    return sum(1 for attrs in read_filled_rows(xlsx, template)
               if normalize_hsn(attrs.get(HSN_HEADER)) is None)


def _requested_ordinal(form):
    """The ordinal of the panel the click came from, sent explicitly as hx-vals.

    It is NOT inferred from the posted keys: the browser posts more than one
    panel's fields whenever anything wider than the panel is included, and
    picking the first ordinal then reports (and refreshes) the wrong panel.
    Returns None when the field is absent or unparseable."""
    try:
        return int(str(form.get("ordinal")))
    except (TypeError, ValueError):
        return None


def _panel_image(product, attrs):
    """The panel photo: the Shopify export's first image when the job has an
    export, else the sheet's own Front Image.

    Only a real URL is used. fill.py falls back to bare local basenames when S3
    hosting is off, and rendering one as an <img src> would show a broken image
    rather than the honest 'no photo' placeholder."""
    if product and product.images:
        return product.images[0]
    front = str(attrs.get("Front Image") or "").strip()
    return front if front.startswith(("http://", "https://")) else None


def _image_slots(attrs):
    """One entry per Myntra image column, numbered from 1 to match the {slot} in
    the replacement key. Only real URLs become thumbnails, for the same reason as
    _panel_image."""
    slots = []
    for slot, header in enumerate(IMAGE_COLUMNS, start=1):
        url = str(attrs.get(header) or "").strip()
        slots.append({"header": header, "slot": slot,
                      "url": url if url.startswith(("http://", "https://")) else None})
    return slots


def _panels(xlsx, csv_path, template, columns, free_columns):
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
            "image": _panel_image(p, attrs),
            "image_slots": _image_slots(attrs),
            # NOT "values": in Jinja `p.values` would resolve to dict.values (the
            # method), silently breaking the pre-selection comparison.
            "chosen": {c: attrs.get(c) for c in columns},
            "free": {c: attrs.get(c) for c in free_columns},
            "filled": _filled_count(attrs, columns, free_columns),
            "hsn": attrs.get(HSN_HEADER),
            "hsn_missing": normalize_hsn(attrs.get(HSN_HEADER)) is None,
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
    columns, free_columns = user_filled_attributes(), user_filled_freetext()
    panels = _panels(xlsx, csv_path, template, columns, free_columns)
    return _templates().TemplateResponse(request, "attributes.html", {
        "user": user, "job_id": job.id, "columns": columns,
        "free_columns": free_columns,
        "free_hints": freetext_hints(free_columns),
        "vocab": attribute_vocab(template, columns),
        "panels": panels,
        "origin": job.result.get("origin", "generate"),
        "filename": job.result.get("filename", ""),
        "edited": job.result.get("edited", False),
        "hsn_gaps": _hsn_gaps_in(panels),
        "total": _total(columns, free_columns)})


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


def _submitted_free(form, columns):
    """Parse free__{ordinal}__{column_index} into {ordinal: {column: raw_value}}."""
    entries = {}
    for key, value in form.items():
        if not key.startswith("free__"):
            continue
        _, ordinal, col_index = key.split("__")
        ordinal, col_index = int(ordinal), int(col_index)
        if 0 <= col_index < len(columns):
            entries.setdefault(ordinal, {})[columns[col_index]] = str(value)
    return entries


def _submitted_hsn(form):
    """Parse hsn__{ordinal} into {ordinal: raw_value}."""
    entries = {}
    for key, value in form.items():
        if key.startswith("hsn__"):
            entries[int(key.split("__")[1])] = str(value)
    return entries


def _build_payload(entries, free, hsn, template, columns, free_columns):
    """Validate every entry and merge dropdown + derived + free-text + HSN values.
    Raises AttributeValueError before anything is written."""
    vocab = attribute_vocab(template, columns)      # built once, not per entry
    has_brand_colour = BRAND_COLOUR_HEADER in template.col_index_by_header
    payload = []
    for ordinal, e in entries.items():
        values = validate_submitted(e["values"], vocab)
        # Derived, never typed — Myntra rejects a null Brand Colour (Remarks).
        if has_brand_colour:
            values[BRAND_COLOUR_HEADER] = derive_brand_colour(values)
        values.update(validate_freetext(free.get(ordinal, {}), free_columns))
        # Only when the panel actually posted the field: a form that omits it
        # must leave the sheet's HSN alone rather than clearing it.
        if ordinal in hsn:
            values[HSN_HEADER] = validate_hsn(hsn[ordinal])
        payload.append({"ordinal": ordinal, "sku": e["sku"], "values": values})
    return payload


async def _save_entries(request, job_id, only=None):
    """Shared by both save routes. Returns (job, ordinals, payload, error, hsn_gaps).

    `only` narrows the write to that single ordinal. The per-panel save relies on
    it: scoping cannot be done in the browser (htmx `hx-include` only ever ADDS
    fields), so the server is what guarantees one click writes one row — and that
    an off-vocabulary value in some other panel cannot fail this one."""
    job, _job_dir, xlsx, _csv = job_files(job_id)
    template = read_template(TEMPLATE)
    columns, free_columns = user_filled_attributes(), user_filled_freetext()
    form = await request.form()
    entries = _submitted(form, columns)
    free = _submitted_free(form, free_columns)
    hsn = _submitted_hsn(form)
    if only is not None:
        entries = {o: e for o, e in entries.items() if o == only}
        free = {o: v for o, v in free.items() if o == only}
        hsn = {o: v for o, v in hsn.items() if o == only}
    ordinals = list(entries)
    try:
        payload = _build_payload(entries, free, hsn, template, columns, free_columns)
        with _WRITE_LOCK:
            write_attributes(xlsx, template, payload)
            # Only after a successful write: a rejected save must not move the
            # registry. The fix-flow rebuild pins HSN from here, so a correction
            # made on this screen has to reach it or a later rebuild undoes it.
            reg_store = sku_registry_store(get_settings(request))
            for e in payload:
                if HSN_HEADER in e["values"]:
                    update_hsn(reg_store, e["sku"], e["values"][HSN_HEADER])
                # Same reasoning for the hand-written names: a rebuild remaps them
                # from the Shopify export, so an unpinned name is silently lost.
                # Only headers this save actually posted, so a per-panel save
                # cannot blank a name another panel owns.
                pinned = {h: e["values"][h] for h in PINNED_NAME_HEADERS
                          if h in e["values"]}
                if pinned:
                    update_names(reg_store, e["sku"], pinned)
    except (AttributeValueError, SkuMismatchError) as exc:
        return job, ordinals, [], str(exc), _hsn_gap_count(xlsx, template)
    return job, ordinals, payload, None, _hsn_gap_count(xlsx, template)


@router.post("/generate/attributes/{job_id}/preview", response_class=HTMLResponse)
async def attributes_live_preview(request: Request, job_id: str):
    get_user(request)
    job_files(job_id)                      # 404s an expired job before doing work
    columns = user_filled_attributes()
    form = await request.form()
    entries = _submitted(form, columns)
    # The post carries every included panel's fields; render the panel that asked.
    entry = entries.get(_requested_ordinal(form)) or {"sku": "", "values": {}}
    attrs = dict(entry["values"])
    attrs["vendorSkuCode"] = entry["sku"]
    return _templates().TemplateResponse(
        request, "_preview_card.html", {"c": build_card(attrs, columns)})


@router.post("/generate/attributes/{job_id}", response_class=HTMLResponse)
async def attributes_save(request: Request, job_id: str):
    get_user(request)
    job, _ordinals, payload, error, hsn_gaps = await _save_entries(request, job_id)
    if error:
        return _templates().TemplateResponse(
            request, "_attr_saved.html", {"job_id": job.id, "error": error})
    return _templates().TemplateResponse(
        request, "_attr_saved.html",
        {"job_id": job.id, "saved": len(payload), "hsn_gaps": hsn_gaps})


@router.post("/generate/attributes/{job_id}/one", response_class=HTMLResponse)
async def attributes_save_one(request: Request, job_id: str):
    """Save a single panel. Same validation and writing as the bulk route; only the
    rendered response differs — a compact inline result plus an out-of-band refresh
    of that panel's filled count.

    Which panel is decided by the explicit `ordinal` field the button sends, never
    by the posted keys; see _requested_ordinal."""
    get_user(request)
    job_files(job_id)                      # 404s an expired job before doing work
    ordinal = _requested_ordinal(await request.form())
    nothing = {"ordinal": 0,
               "error": "Nothing to save — please reload the screen and try again."}
    if ordinal is None:
        # No usable ordinal, so no panel to report against. Falling back to the
        # first posted one would silently stamp a wrong "0/14 filled" onto an
        # untouched panel. The error branch emits no out-of-band span, so no
        # count is disturbed.
        return _templates().TemplateResponse(request, "_attr_panel_saved.html",
                                             nothing)
    job, ordinals, payload, error, hsn_gaps = await _save_entries(request, job_id,
                                                                  only=ordinal)
    if not ordinals:
        # The requested panel posted no sku__N / attr__N__* keys of its own.
        return _templates().TemplateResponse(request, "_attr_panel_saved.html",
                                             nothing)
    if error:
        return _templates().TemplateResponse(
            request, "_attr_panel_saved.html",
            {"ordinal": ordinal, "error": error})
    columns, free_columns = user_filled_attributes(), user_filled_freetext()
    values = payload[0]["values"] if payload else {}
    return _templates().TemplateResponse(
        request, "_attr_panel_saved.html",
        {"ordinal": ordinal,
         "filled": _filled_count(values, columns, free_columns),
         "total": _total(columns, free_columns),
         "hsn_gaps": hsn_gaps})
