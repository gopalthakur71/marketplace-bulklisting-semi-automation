import os


def upload_images(paths, bucket, prefix="", base_dir=None, region=None, client=None):
    """Upload the given local JPG file paths to s3://bucket/<prefix>/<key> with a
    public-image content type, so Myntra can fetch each by a .jpg URL.

    The S3 key tail mirrors the local layout: if `base_dir` is given, it is each
    file's path relative to `base_dir` (so <base_dir>/<sku>/<n>.jpg -> <prefix>/<sku>/<n>.jpg);
    otherwise it is just the basename. This matches the URL the sheet references
    (public_base_url + "/" + same tail).

    Only the paths passed are uploaded — the caller supplies exactly this run's
    validated images, so stale files from earlier batches are not re-uploaded.
    Returns the uploaded keys. `client` is injectable for testing.
    """
    if client is None:
        import boto3
        client = boto3.client("s3", region_name=region)
    prefix = (prefix or "").strip("/")
    keys = []
    for path in paths:
        if not path.lower().endswith((".jpg", ".jpeg")):
            continue
        rel = os.path.relpath(path, base_dir) if base_dir else os.path.basename(path)
        rel = rel.replace(os.sep, "/")   # S3 keys always use forward slashes
        key = f"{prefix}/{rel}" if prefix else rel
        client.upload_file(path, bucket, key, ExtraArgs={"ContentType": "image/jpeg"})
        keys.append(key)
    return keys
