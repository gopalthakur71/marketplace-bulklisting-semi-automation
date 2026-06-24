import io
import os

from PIL import Image

from src.models import ImageResult


def _http_fetch(url):
    import requests
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.content


def flatten_to_jpg(img, quality, out_path):
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, rgba)
    img = img.convert("RGB")
    img.save(out_path, "JPEG", quality=quality)


def validate_image(path, specs):
    size = os.path.getsize(path)
    if size > specs["max_bytes"]:
        return f"file size {size} exceeds max {specs['max_bytes']}"
    with Image.open(path) as im:
        w, h = im.size
    if w < specs["min_width"] or h < specs["min_height"]:
        return f"dimensions {w}x{h} below minimum {specs['min_width']}x{specs['min_height']}"
    return None


def process_images(product, specs, out_dir, fetch=_http_fetch):
    os.makedirs(out_dir, exist_ok=True)
    res = ImageResult(sku=product.sku)
    max_images = specs.get("max_images", 7)
    quality = specs.get("quality", 90)
    for i, url in enumerate(product.images[:max_images], start=1):
        name = f"{product.sku}_{i}.jpg"
        out_path = os.path.join(out_dir, name)
        try:
            data = fetch(url)
            with Image.open(io.BytesIO(data)) as im:
                flatten_to_jpg(im, quality, out_path)
        except Exception as e:  # download/convert failure
            res.failed.append((name, f"convert error: {e}"))
            continue
        reason = validate_image(out_path, specs)
        res.jpgs.append(out_path)
        if reason:
            res.failed.append((name, reason))
        else:
            res.passed.append(out_path)
    return res
