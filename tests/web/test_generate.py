import io
from unittest import mock

from fastapi.testclient import TestClient

from src.web.main import create_app
from src.web.settings import Settings
import src.web.routers.generate as gen


def _client(tmp_path):
    s = Settings(auth_disabled=True, s3_bucket="b",
                 ledger_local_path=str(tmp_path / "led.json"))
    return TestClient(create_app(s)), s


def test_generate_rejects_non_csv(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/generate", files={"file": ("notes.txt", b"hi", "text/plain")})
    assert r.status_code == 400


def test_generate_runs_job_and_confirm_advances_ledger(tmp_path, monkeypatch):
    client, settings = _client(tmp_path)

    # Stub the heavy pipeline: pretend it wrote a file for 3 products.
    def fake_main(csv_path=None, out_dir=None, style_group_id_start=None, **kw):
        with open(f"{out_dir}/myntra_filled.xlsx", "wb") as fh:
            fh.write(b"xlsx-bytes")
        with open(f"{out_dir}/report.txt", "w") as fh:
            fh.write("3 rows\n1 vocab flag: Ivory\n")
        return {"filled": f"{out_dir}/myntra_filled.xlsx",
                "report": f"{out_dir}/report.txt", "products": 3, "uploaded": 9}

    monkeypatch.setattr(gen, "pipeline_main", fake_main)
    # count products from CSV deterministically (3 data rows)
    monkeypatch.setattr(gen, "count_products", lambda path: 3)

    csv = b"Handle,Title\na,A\nb,B\nc,C\n"
    r = client.post("/generate", files={"file": ("products_export.csv", csv, "text/csv")})
    assert r.status_code == 200
    job_id = r.headers["x-job-id"]

    # Background task runs inline under TestClient; poll once.
    # Add retry loop in case the thread hasn't finished yet.
    import time
    poll = None
    for _ in range(20):
        poll = client.get(f"/jobs/{job_id}")
        if "Download" in poll.text:
            break
        time.sleep(0.05)

    assert poll.status_code == 200
    assert "Download" in poll.text
    assert "16" in poll.text or "1 –" in poll.text or "1 - 3" in poll.text  # range shown

    # ledger started empty (next id 1) -> reserve was [1,3]; confirm advances to 4
    rc = client.post(f"/generate/confirm/{job_id}")
    assert rc.status_code == 200
    from src.myntra.groupid_ledger import read_ledger
    from src.web.settings import ledger_store
    led = read_ledger(ledger_store(settings))
    assert led["next_style_group_id"] == 4
