import dataclasses
import html
import json
import os
import re
import shutil
import uuid

import yaml
from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from src.myntra.error_reader import load_rules
from src.myntra.error_sources import detect_format, read_error_file
from src.myntra.explainer import explain_item, ExplainedIssue
from src.myntra.corrector import correct_from_issues, regenerate_surface_b
from src.myntra.template_reader import read_template
from src.web.settings import explanation_store, correction_log_store
from src.web.routers.pages import get_user, get_settings

router = APIRouter()
RUNTIME = os.path.join(os.path.dirname(os.path.dirname(__file__)), "runtime")
CONSTANTS = os.path.join("config", "myntra", "constants.yaml")
TEMPLATE = os.path.join("templates", "myntra", "Myntra-Sku-Template-2026-06-16.xlsx")

_ACCEPTED_EXT = (".xlsx", ".csv")


def _safe_fix_id(fix_id: str) -> str:
    """Validate fix_id is a 32-char hex string (uuid4().hex format).
    Raises HTTP 404 for anything that doesn't match to prevent path traversal."""
    if not re.fullmatch(r"[0-9a-f]{32}", fix_id):
        raise HTTPException(status_code=404, detail="unknown fix session")
    return fix_id


def _fix_dir(fix_id: str) -> str:
    fix_dir = os.path.join(RUNTIME, "fix-" + fix_id)
    if not os.path.realpath(fix_dir).startswith(os.path.realpath(RUNTIME) + os.sep):
        raise HTTPException(status_code=404, detail="unknown fix session")
    return fix_dir


def _templates():
    from src.web.main import templates
    return templates


def _resolve_template_path():
    return TEMPLATE


def _load_constants():
    with open(CONSTANTS, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _gemini_cfg(settings):
    return {"enabled": bool(settings.explain_with_gemini and settings.gemini_api_key),
            "api_key": settings.gemini_api_key, "model": settings.gemini_model,
            "client": None}


@router.get("/fix", response_class=HTMLResponse)
def fix_form(request: Request):
    get_user(request)
    return _templates().TemplateResponse(request, "fix.html", {"user": get_user(request)})


@router.get("/fix/dismiss", response_class=HTMLResponse)
def fix_dismiss(request: Request):
    get_user(request)
    return HTMLResponse(
        '<div class="panel"><h3>No changes made</h3>'
        '<p>Nothing was written. Fix the items listed above, then re-upload when ready.</p></div>')


@router.post("/fix", response_class=HTMLResponse)
def fix_upload(request: Request, file: UploadFile = File(...)):
    get_user(request)
    settings = get_settings(request)
    if not file.filename.lower().endswith(_ACCEPTED_EXT):
        raise HTTPException(status_code=400, detail="Please upload a Myntra .xlsx or .csv file")

    fix_id = uuid.uuid4().hex
    fix_dir = os.path.join(RUNTIME, "fix-" + fix_id)
    os.makedirs(fix_dir, exist_ok=True)
    ext = os.path.splitext(file.filename)[1].lower()
    err_path = os.path.join(fix_dir, "rejection" + ext)
    with open(err_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    source_type, reason = detect_format(err_path)
    if source_type is None:
        return HTMLResponse('<div class="panel"><h3>Unrecognised file</h3><p>%s</p></div>' % reason)

    rules = load_rules()
    store = explanation_store(settings)
    gem = _gemini_cfg(settings)
    items = read_error_file(err_path, rules)
    issues = [explain_item(it, rules, store=store, gemini=gem) for it in items]

    with open(os.path.join(fix_dir, "issues.json"), "w", encoding="utf-8") as fh:
        json.dump({"source_type": source_type,
                   "issues": [dataclasses.asdict(i) for i in issues]}, fh)

    correctable = [i for i in issues if i.action != "explain_only"]
    explain_only = [i for i in issues if i.action == "explain_only"]
    # Surface B (whole-sheet csv / per-SKU Listings Report) rebuilds SKUs by
    # re-running the pipeline, which needs the Shopify products export. Prod has
    # no baked-in export, so ask the user to upload it alongside the fix.
    # Either a Surface-B correctable fix OR a manual-fix rebuild (of the
    # explain-only SKUs) re-runs the pipeline, which needs the Shopify export.
    needs_export = (
        (source_type in ("sheet_csv", "listings_report") and bool(correctable))
        or bool(explain_only)
    )
    resp = _templates().TemplateResponse(request, "_fix_review.html", {
        "correctable": correctable, "explain_only": explain_only,
        "fix_id": fix_id, "needs_export": needs_export})
    resp.headers["x-fix-id"] = fix_id
    return resp


def _error_panel(exc):
    """Render any apply failure as a 200 panel. htmx only swaps on 2xx, so a bare
    500 would leave the Proceed button looking dead (the original prod bug)."""
    # Exception text can carry user-influenced content (e.g. an uploaded filename
    # in a FileNotFoundError). Escape it so it can never break out of the <pre>.
    detail = html.escape(str(exc) or exc.__class__.__name__)
    return HTMLResponse(
        '<div class="panel"><h3>Something went wrong</h3>'
        '<p>We could not complete the fix. Please try again, and if it keeps '
        'happening share this detail with support:</p><pre>%s</pre></div>' % detail)


def _export_prompt_panel():
    return HTMLResponse(
        '<div class="panel"><h3>We need your Shopify products export</h3>'
        '<p>To rebuild these SKUs we re-run the listing pipeline, which needs the '
        'Shopify <strong>products export</strong> you generated them from. Go back, '
        'attach that CSV in the box next to &ldquo;Proceed&rdquo;, and try again.</p></div>')


def _save_export(upload, fix_dir):
    """Persist an uploaded Shopify products export to the fix dir; return its path,
    or None if no usable file was submitted."""
    if upload is None or not getattr(upload, "filename", None):
        return None
    path = os.path.join(fix_dir, "products_export.csv")
    with open(path, "wb") as out:
        shutil.copyfileobj(upload.file, out)
    return path


def _load_issues(fix_dir):
    path = os.path.join(fix_dir, "issues.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="session expired, please re-upload")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data["source_type"], [ExplainedIssue(**d) for d in data["issues"]]


@router.post("/fix/apply/{fix_id}", response_class=HTMLResponse)
async def fix_apply(request: Request, fix_id: str):
    get_user(request)
    settings = get_settings(request)
    fix_id = _safe_fix_id(fix_id)
    fix_dir = _fix_dir(fix_id)
    try:
        return await _fix_apply(request, settings, fix_id, fix_dir)
    except HTTPException:
        raise  # 404 for expired/unknown session stays a real error
    except Exception as exc:  # noqa: BLE001 - any failure -> visible panel, not a dead button
        return _error_panel(exc)


async def _fix_apply(request, settings, fix_id, fix_dir):
    source_type, issues = _load_issues(fix_dir)

    form = await request.form()
    answers, submitted_drops, export_upload = {}, set(), None
    action = "fix"
    for key, value in form.items():
        if key == "action":
            action = str(value) or "fix"
        elif key == "products_export":
            export_upload = value
        elif key.startswith("answer__") and str(value).strip():
            _, sku, field = key.split("__", 2)
            answers.setdefault(sku, {})[field] = value
        elif key.startswith("drop__"):
            submitted_drops.add(key.split("__", 1)[1])

    out_path = os.path.join(fix_dir, "myntra_corrected.xlsx")
    if action == "manual":
        # Rebuild ONLY the explain-only SKUs into a fresh Myntra sheet, pinning the
        # original HSN/styleGroupId (regenerate_surface_b reads the registry). This
        # is the "I fixed the photo in Shopify, re-export, give me a sheet" path.
        skus = sorted({i.sku for i in issues
                       if i.sku and i.action == "explain_only"})
        if not skus:
            summary = {"written": 0, "file": None, "fixed": [], "could_not_rebuild": [],
                       "dropped": [], "rejected": {}, "changed": {}, "manual_needed": []}
            return _templates().TemplateResponse(request, "_fix_result.html",
                                                 {"summary": summary, "fix_id": fix_id})
        csv_path = _save_export(export_upload, fix_dir)
        if csv_path is None:
            return _export_prompt_panel()
        summary = regenerate_surface_b(skus, settings, fix_dir, csv_path=csv_path)
        if summary.get("file") and os.path.exists(summary["file"]):
            shutil.copyfile(summary["file"], out_path)
        return _templates().TemplateResponse(request, "_fix_result.html",
                                             {"summary": summary, "fix_id": fix_id})

    if source_type == "sku_xlsx":
        template = read_template(_resolve_template_path())
        summary = correct_from_issues(
            issues, template, _resolve_template_path(), _load_constants(),
            answers, out_path, log_store=correction_log_store(settings), fix_id=fix_id,
            drops=submitted_drops)
    else:
        if source_type == "sheet_csv":
            skus = None  # whole-sheet rejection: rebuild the entire sheet
        else:  # listings_report: per-SKU
            skus = sorted({i.sku for i in issues
                           if i.sku and i.action != "explain_only"
                           and i.sku not in submitted_drops})
            if not skus:
                # Everything correctable was dropped or explain_only -> there is
                # nothing to rebuild. Do NOT pass None to regenerate_surface_b:
                # that sentinel means "rebuild the whole catalog", which would
                # silently rebuild a set the user never asked for.
                summary = {"written": 0, "file": None, "fixed": [],
                           "could_not_rebuild": [], "dropped": sorted(submitted_drops),
                           "rejected": {}, "changed": {},
                           "manual_needed": [{"sku": i.sku, "explanation": i.explanation}
                                             for i in issues if i.action == "explain_only"]}
                return _templates().TemplateResponse(request, "_fix_result.html",
                                                     {"summary": summary, "fix_id": fix_id})
        # A real Surface-B rebuild needs the Shopify products export. Prod has no
        # baked-in export, so require the user to upload it rather than 500.
        csv_path = _save_export(export_upload, fix_dir)
        if csv_path is None:
            return _export_prompt_panel()
        summary = regenerate_surface_b(skus, settings, fix_dir, csv_path=csv_path)
        if summary.get("file") and os.path.exists(summary["file"]):
            shutil.copyfile(summary["file"], out_path)
        # regenerate_surface_b always returns manual_needed=[] (key present but
        # empty), so a plain setdefault() would be a no-op and silently drop the
        # explain_only issues on this path. Use `or` so the fallback list wins
        # whenever the corrector didn't actually populate it.
        summary["manual_needed"] = summary.get("manual_needed") or [
            {"sku": i.sku, "explanation": i.explanation}
            for i in issues if i.action == "explain_only"]

    return _templates().TemplateResponse(request, "_fix_result.html",
                                         {"summary": summary, "fix_id": fix_id})


@router.get("/fix/download/{fix_id}")
def fix_download(request: Request, fix_id: str):
    get_user(request)
    fix_id = _safe_fix_id(fix_id)
    fix_dir = _fix_dir(fix_id)
    path = os.path.join(fix_dir, "myntra_corrected.xlsx")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="not ready")
    return FileResponse(path, filename="myntra_corrected.xlsx")
