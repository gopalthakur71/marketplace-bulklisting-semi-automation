import os
import tempfile

from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse

from src.myntra.template_reader import read_template
from src.myntra.preview import read_filled_rows, build_card
from src.myntra.attribute_entry import user_filled_attributes
from src.myntra.pipeline import DEFAULT_TEMPLATE_NAME
from src.web.routers.pages import get_user

router = APIRouter()
TEMPLATE = os.path.join("templates", "myntra", DEFAULT_TEMPLATE_NAME)


def _templates():
    from src.web.main import templates
    return templates


@router.get("/preview", response_class=HTMLResponse)
def preview_form(request: Request):
    user = get_user(request)
    return _templates().TemplateResponse(request, "preview.html", {"user": user})


@router.post("/preview", response_class=HTMLResponse)
async def preview_submit(request: Request, file: UploadFile = File(...)):
    get_user(request)
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Please upload the filled .xlsx file")
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    with open(path, "wb") as out:
        out.write(await file.read())
    try:
        template = read_template(TEMPLATE)
        rows = read_filled_rows(path, template)
    finally:
        os.remove(path)
    user_filled = user_filled_attributes()
    cards = [build_card(attrs, user_filled) for attrs in rows]
    return _templates().TemplateResponse(request, "_preview.html", {"cards": cards})
