import io
import warnings

import pytest
from PIL import Image

from src.myntra.pipeline import main, BuildCancelled

TEMPLATE = "templates/myntra/Myntra-Sku-Template-2026-06-16.xlsx"
CSV = "tests/fixtures/products_export.csv"          # 2 products


def _fake_fetch():
    buf = io.BytesIO()
    Image.new("RGB", (1000, 1000), (10, 20, 30)).save(buf, "PNG")
    data = buf.getvalue()
    return lambda url: data


def _run(out_dir, should_cancel):
    return main(template_path=TEMPLATE, csv_path=CSV, out_dir=str(out_dir),
                config_dir="config/myntra", fetch=_fake_fetch(), upload=False,
                should_cancel=should_cancel)


class _CancelAfter:
    """False for the first n checks, True from then on."""

    def __init__(self, n):
        self.n = n
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.calls > self.n


def test_cancel_before_first_product_writes_no_workbook(tmp_path):
    warnings.filterwarnings("ignore")
    out = tmp_path / "out"
    with pytest.raises(BuildCancelled):
        _run(out, _CancelAfter(0))
    assert not (out / "myntra_filled.xlsx").exists()


def test_cancel_part_way_through_products_writes_no_workbook(tmp_path):
    warnings.filterwarnings("ignore")
    out = tmp_path / "out"
    stop = _CancelAfter(1)                 # first product runs, second is cancelled
    with pytest.raises(BuildCancelled):
        _run(out, stop)
    assert not (out / "myntra_filled.xlsx").exists()


def test_cancel_between_last_product_and_fill_writes_no_workbook(tmp_path):
    warnings.filterwarnings("ignore")
    out = tmp_path / "out"
    with pytest.raises(BuildCancelled):    # 2 products pass, the pre-fill check trips
        _run(out, _CancelAfter(2))
    assert not (out / "myntra_filled.xlsx").exists()


def test_should_cancel_never_true_completes_normally(tmp_path):
    warnings.filterwarnings("ignore")
    out = tmp_path / "out"
    res = _run(out, lambda: False)
    assert res["products"] == 2
    assert (out / "myntra_filled.xlsx").exists()


def test_should_cancel_omitted_is_unchanged_behaviour(tmp_path):
    """Every existing caller passes nothing — that path must not change."""
    warnings.filterwarnings("ignore")
    out = tmp_path / "out"
    res = main(template_path=TEMPLATE, csv_path=CSV, out_dir=str(out),
               config_dir="config/myntra", fetch=_fake_fetch(), upload=False)
    assert res["products"] == 2
    assert (out / "myntra_filled.xlsx").exists()
