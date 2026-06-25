from src.core.s3_upload import upload_images


class _StubS3:
    def __init__(self):
        self.calls = []

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        self.calls.append((filename, bucket, key, ExtraArgs))


def test_upload_images_keys_and_content_type(tmp_path):
    jpg1 = tmp_path / "A_1.jpg"; jpg1.write_bytes(b"x")
    jpg2 = tmp_path / "A_2.jpeg"; jpg2.write_bytes(b"x")
    txt = tmp_path / "report.txt"; txt.write_text("skip me")  # non-image ignored

    client = _StubS3()
    # Caller passes explicit paths (this run's images), not a directory.
    keys = upload_images([str(jpg1), str(jpg2), str(txt)], "mybucket",
                         prefix="myntra/", client=client)

    assert keys == ["myntra/A_1.jpg", "myntra/A_2.jpeg"]
    assert all(c[1] == "mybucket" for c in client.calls)
    assert all(c[3] == {"ContentType": "image/jpeg"} for c in client.calls)
    # the .txt was not uploaded
    assert len(client.calls) == 2


def test_upload_images_mirrors_per_sku_folders_with_base_dir(tmp_path):
    sku = tmp_path / "SKU1"
    sku.mkdir()
    (sku / "1.jpg").write_bytes(b"x")
    (sku / "2.jpg").write_bytes(b"x")

    client = _StubS3()
    keys = upload_images([str(sku / "1.jpg"), str(sku / "2.jpg")], "b",
                         prefix="myntra", base_dir=str(tmp_path), client=client)

    # S3 key mirrors the local <sku>/<n>.jpg layout, with forward slashes.
    assert keys == ["myntra/SKU1/1.jpg", "myntra/SKU1/2.jpg"]
