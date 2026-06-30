from fastapi import APIRouter, Request

from src.web.auth import current_user

router = APIRouter()


def get_settings(request: Request):
    return request.app.state.settings


def get_user(request: Request):
    settings = request.app.state.settings
    token = (request.cookies.get("id_token")
             or (request.headers.get("authorization", "").removeprefix("Bearer ").strip() or None))
    return current_user(settings, token)


@router.get("/")
def home(request: Request):
    from src.web.main import templates
    user = get_user(request)
    return templates.TemplateResponse("home.html", {"request": request, "user": user})
