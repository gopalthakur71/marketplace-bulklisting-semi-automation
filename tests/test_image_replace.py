import io
import os

from PIL import Image

from src.myntra.image_replace import prepare, replacement_key


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "red").save(buf, "PNG")
    return buf.getvalue()


def test_replacement_key_changes_with_the_photo():
    """The whole feature turns on this. The build path writes {sku}/{n}.jpg; re-using
    that key hands Myntra a URL identical to the one it already rejected, which it
    may never re-fetch. A different photo must produce a different URL."""
    a, b = _png(800, 800), _png(800, 801)
    assert replacement_key("S1", 1, a) != replacement_key("S1", 1, b)


def test_replacement_key_is_stable_for_the_same_photo():
    """Re-uploading the same file twice must not litter the bucket."""
    a = _png(800, 800)
    assert replacement_key("S1", 1, a) == replacement_key("S1", 1, a)
    assert replacement_key("S1", 1, a).startswith("S1/1-")
    assert replacement_key("S1", 1, a).endswith(".jpg")


def test_prepare_rejects_an_undersized_photo(tmp_path):
    """Myntra's floor is 700x700. Catching it here means one clear message instead
    of a whole-file rejection days later."""
    specs = {"min_width": 700, "min_height": 700, "max_bytes": 10485760, "quality": 90}
    path, key, reason = prepare("S1", 1, _png(500, 500), specs, str(tmp_path))
    assert path is None and key is None
    assert "500x500" in reason and "700x700" in reason


def test_prepare_converts_and_keeps_a_valid_photo(tmp_path):
    specs = {"min_width": 700, "min_height": 700, "max_bytes": 10485760, "quality": 90}
    path, key, reason = prepare("S1", 3, _png(800, 800), specs, str(tmp_path))
    assert reason is None
    assert key.startswith("S1/3-") and key.endswith(".jpg")
    assert os.path.exists(path)
    with Image.open(path) as im:
        assert im.format == "JPEG"


def test_prepare_reports_a_file_that_is_not_an_image(tmp_path):
    """A PDF or a .txt renamed to .jpg fails its own slot, never the request."""
    specs = {"min_width": 700, "min_height": 700, "max_bytes": 10485760, "quality": 90}
    path, key, reason = prepare("S1", 1, b"not an image", specs, str(tmp_path))
    assert path is None
    assert "convert error" in reason
