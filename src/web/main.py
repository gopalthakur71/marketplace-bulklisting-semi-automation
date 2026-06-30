import os

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.web.auth import AuthError
from src.web.settings import load_settings

_HERE = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(_HERE, "templates"))


def create_app(settings=None) -> FastAPI:
    app = FastAPI(title="Marigold Ops")
    app.state.settings = settings or load_settings()
    app.mount("/static", StaticFiles(directory=os.path.join(_HERE, "static")), name="static")

    from src.web.routers import pages
    app.include_router(pages.router)
    # generate + fix routers are added in Tasks 5 and 6 (see those tasks).

    @app.exception_handler(AuthError)
    async def _auth_handler(request: Request, exc: AuthError):
        return JSONResponse({"detail": "login required"}, status_code=401)

    return app


app = create_app()
