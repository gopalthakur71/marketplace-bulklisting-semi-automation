"""Replace a product's images from files the owner supplies, after Myntra rejects
a photo.

Separate from core/images.py because the source differs: that module fetches a
product's images from Shopify URLs during a build. This one takes bytes uploaded
in the browser. What the two share — flattening to JPG and validating against the
Myntra specs — is imported, not duplicated."""
import hashlib
import io
import os

import yaml
from PIL import Image

from src.core.images import flatten_to_jpg, validate_image


class ImageConfigError(Exception):
    """Image hosting is not configured, so no public URL can be produced."""


def load_specs(config_dir="config/myntra"):
    with open(os.path.join(config_dir, "image_specs.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def replacement_key(sku, slot, data):
    """S3 key tail for a replacement image: {sku}/{slot}-{hash}.jpg.

    Hashing the file's own bytes is what makes replacement work at all. The build
    path writes {sku}/{slot}.jpg; writing a replacement there would overwrite the
    object and leave Myntra with a URL byte-identical to the one it already
    rejected — which it may never re-fetch, so the new photo would never be seen.
    A different photo therefore yields a different URL, while re-uploading the
    same file twice stays idempotent instead of littering the bucket."""
    digest = hashlib.sha256(data).hexdigest()[:8]
    return f"{sku}/{slot}-{digest}.jpg"


def prepare(sku, slot, data, specs, out_dir):
    """Convert one uploaded file to a validated JPG on disk.

    Returns (local_path, key, None) on success, or (None, None, reason) when the
    photo cannot be used. Never raises on bad input: a corrupt upload is a
    per-slot message the owner can act on, not a failed request that loses the
    other slots he supplied in the same click."""
    key = replacement_key(sku, slot, data)
    out_path = os.path.join(out_dir, key.replace("/", os.sep))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    try:
        with Image.open(io.BytesIO(data)) as im:
            flatten_to_jpg(im, specs.get("quality", 90), out_path)
    except Exception as exc:
        return None, None, f"convert error: {exc}"
    reason = validate_image(out_path, specs)
    if reason:
        return None, None, reason
    return out_path, key, None
