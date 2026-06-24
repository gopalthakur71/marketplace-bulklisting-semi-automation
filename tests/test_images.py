import io
import os

from PIL import Image

from src.images import flatten_to_jpg, validate_image, process_images
from src.models import Product


def test_flatten_transparency_not_black(tmp_path):
    img = Image.new("RGBA", (800, 800), (0, 0, 0, 0))
    out = tmp_path / "t.jpg"
    flatten_to_jpg(img, 90, str(out))
    jpg = Image.open(out).convert("RGB")
    assert jpg.getpixel((400, 400)) == (255, 255, 255)
    assert out.suffix == ".jpg"


def test_validate_min_dimensions(tmp_path):
    small = tmp_path / "s.jpg"
    Image.new("RGB", (100, 100), (255, 0, 0)).save(small, "JPEG")
    specs = {"min_width": 700, "min_height": 700, "max_bytes": 10485760}
    reason = validate_image(str(small), specs)
    assert reason is not None and "dimension" in reason.lower()


def test_process_images_names_and_counts(tmp_path):
    buf = io.BytesIO()
    Image.new("RGBA", (1000, 1000), (10, 20, 30, 255)).save(buf, "PNG")
    data = buf.getvalue()

    def fake_fetch(url):
        return data

    p = Product(handle="h", sku="S1", title="t", vendor="v", tags="", body_html="",
                price=1.0, compare_at_price=None, color=None, fabric=None,
                size=None, status="active", images=["u1", "u2"])
    specs = {"min_width": 700, "min_height": 700, "max_bytes": 10485760,
             "quality": 90, "max_images": 7}
    res = process_images(p, specs, str(tmp_path), fetch=fake_fetch)
    assert os.path.basename(res.jpgs[0]) == "S1_1.jpg"
    assert os.path.basename(res.jpgs[1]) == "S1_2.jpg"
    assert len(res.passed) == 2
    assert res.passed_urls == ["u1", "u2"]   # CDN URLs tracked for the sheet
    assert res.failed == []
