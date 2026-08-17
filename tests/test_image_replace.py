import io
import os

import pytest
from PIL import Image

from src.myntra.image_replace import ImageConfigError, host, prepare, replacement_key


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


@pytest.mark.parametrize("bad_sku", ["../../evil", "C:/Windows/Temp/pwn", ".."])
def test_replacement_key_rejects_unsafe_sku(bad_sku):
    """sku ends up in a filesystem path and an S3 key. An unsanitized value like
    a ".." segment or an absolute path is a path-traversal / arbitrary-file-write
    hole, not just a formatting quirk — it must be rejected outright."""
    with pytest.raises(ValueError):
        replacement_key(bad_sku, 1, b"data")


def test_prepare_does_not_escape_out_dir_for_a_path_traversal_sku(tmp_path):
    """A malicious sku must fail its own slot, and critically must not write
    anything outside out_dir — not even before returning the failure reason."""
    specs = {"min_width": 700, "min_height": 700, "max_bytes": 10485760, "quality": 90}
    path, key, reason = prepare("../../evil", 1, _png(800, 800), specs, str(tmp_path))
    assert path is None and key is None
    assert reason is not None
    escaped = os.path.abspath(os.path.join(str(tmp_path), "..", "..", "evil"))
    assert not os.path.exists(escaped)


def test_prepare_does_not_raise_for_windows_invalid_sku_characters(tmp_path):
    """A sku containing characters Windows forbids in filenames must fail its own
    slot with a reason, not raise an OSError that aborts the whole upload."""
    specs = {"min_width": 700, "min_height": 700, "max_bytes": 10485760, "quality": 90}
    path, key, reason = prepare('S<>:"|?*1', 1, _png(800, 800), specs, str(tmp_path))
    assert path is None and key is None
    assert reason is not None


class _FakeS3:
    def __init__(self):
        self.calls = []

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.calls.append((path, bucket, key, ExtraArgs))


def test_host_returns_public_urls_matching_the_uploaded_keys(tmp_path):
    specs = {"public_base_url": "https://cdn.example/myntra", "s3_bucket": "b",
             "s3_prefix": "myntra", "s3_upload": True}
    p = tmp_path / "S1" / "1-abcd1234.jpg"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    client = _FakeS3()
    urls = host([(str(p), "S1/1-abcd1234.jpg")], specs, str(tmp_path), client=client)
    assert urls == ["https://cdn.example/myntra/S1/1-abcd1234.jpg"]
    assert client.calls[0][1:3] == ("b", "myntra/S1/1-abcd1234.jpg")


def test_host_refuses_when_hosting_is_not_configured(tmp_path):
    """Without a public base URL there is no URL to write. Writing a local path into
    a column Myntra reads as a URL fails at upload with a message pointing nowhere
    near here, so fail loudly and early instead."""
    with pytest.raises(ImageConfigError):
        host([("/tmp/x.jpg", "S1/1-a.jpg")],
             {"public_base_url": "", "s3_bucket": "b", "s3_upload": True},
             str(tmp_path), client=_FakeS3())
