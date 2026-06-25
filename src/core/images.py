import io
import os

from PIL import Image

from src.core.models import ImageResult


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
    res = ImageResult(sku=product.sku)
    max_images = specs.get("max_images", 7)
    quality = specs.get("quality", 90)
    public_base = (specs.get("public_base_url") or "").rstrip("/")
    # Each SKU's images live in their own folder: <out_dir>/<sku>/<n>.jpg, mirrored
    # to S3 as <prefix>/<sku>/<n>.jpg.
    sku_dir = os.path.join(out_dir, product.sku)
    os.makedirs(sku_dir, exist_ok=True)
    for i, url in enumerate(product.images[:max_images], start=1):
        relkey = f"{product.sku}/{i}.jpg"   # path under the images dir == S3 key tail
        out_path = os.path.join(sku_dir, f"{i}.jpg")
        try:
            data = fetch(url)
            with Image.open(io.BytesIO(data)) as im:
                flatten_to_jpg(im, quality, out_path)
        except Exception as e:  # download/convert failure
            res.failed.append((relkey, f"convert error: {e}"))
            continue
        reason = validate_image(out_path, specs)
        res.jpgs.append(out_path)
        if reason:
            res.failed.append((relkey, reason))
        else:
            res.passed.append(out_path)
            # Myntra ingests images by URL and requires a .jpg/.jpeg extension. Write
            # the public S3 URL of the converted JPG (key mirrors the local path);
            # fall back to the source CDN URL only if no public host is configured.
            res.passed_urls.append(f"{public_base}/{relkey}" if public_base else url)
    return res
