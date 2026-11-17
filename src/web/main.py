import logging
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.web.settings import load_settings

# Surface app INFO logs (e.g. the dedup-guard decision). uvicorn only configures
# its own loggers, leaving the root at lastResort (WARNING), so our INFO lines are
# otherwise dropped. basicConfig is a no-op if a handler is already installed.
logging.basicConfig(level=logging.INFO)

_HERE = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(_HERE, "templates"))


def _asset_v(filename):
    """Cache-busting token = mtime of a /static file, so a CSS/JS edit always
    forces the browser to re-fetch (otherwise the stylesheet is cached and edits
    never show without a manual hard refresh)."""
    try:
        return int(os.path.getmtime(os.path.join(_HERE, "static", filename)))
    except OSError:
        return 0


templates.env.globals["asset_v"] = _asset_v


def create_app(settings=None) -> FastAPI:
    app = FastAPI(title="Marigold Ops")
    app.state.settings = settings or load_settings()
    app.mount("/static", StaticFiles(directory=os.path.join(_HERE, "static")), name="static")

    from src.web.routers import pages, generate, fix, preview, attributes
    app.include_router(pages.router)
    app.include_router(generate.router)
    app.include_router(fix.router)
    app.include_router(preview.router)
    app.include_router(attributes.router)
    return app


app = create_app()
