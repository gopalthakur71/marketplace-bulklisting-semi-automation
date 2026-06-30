from fastapi.testclient import TestClient

from src.web.main import create_app
from src.web.settings import Settings


def _client():
    return TestClient(create_app(Settings(auth_disabled=True, s3_bucket="b")))


def test_home_page_renders():
    r = _client().get("/")
    assert r.status_code == 200
    assert "Generate" in r.text
    assert "Fix" in r.text


def test_static_css_served():
    r = _client().get("/static/app.css")
    assert r.status_code == 200
    assert "#E8A33D" in r.text  # marigold accent present
