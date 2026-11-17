from dataclasses import dataclass

from fastapi import APIRouter, Request

router = APIRouter()


@dataclass
class User:
    email: str


# No login: the app is reachable only through an SSM port forward, so AWS IAM (the `gt`
# profile that opens the session) is the gate. Cognito was removed in IaC phase 5
# (docs/decisions/2026-11-17-remove-cognito-iam-only-access.md).
OPERATOR = User(email="ijor-ops")


def get_settings(request: Request):
    return request.app.state.settings


def get_user(request: Request):
    return OPERATOR


@router.get("/")
def home(request: Request):
    from src.web.main import templates
    user = get_user(request)
    return templates.TemplateResponse(request, "home.html", {"user": user})
