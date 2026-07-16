import pytest
from io import BytesIO

from app.storage import ImmutableObjectExists, LocalOriginalStorage, S3CompatibleOriginalStorage, StorageError


def test_local_storage_is_immutable_and_confined(tmp_path):
    storage = LocalOriginalStorage(tmp_path / "objects")
    storage.put("papers/id/original.txt", b"first")
    assert storage.path_for("papers/id/original.txt").read_bytes() == b"first"

    with pytest.raises(ImmutableObjectExists):
        storage.put("papers/id/original.txt", b"replacement")
    assert storage.path_for("papers/id/original.txt").read_bytes() == b"first"

    for unsafe in ("../secret", "/absolute", "papers/../../secret", "papers\\escape"):
        with pytest.raises(StorageError):
            storage.path_for(unsafe)


def test_originals_and_assets_are_routed_to_separate_roots(tmp_path):
    originals, assets = tmp_path / "originals", tmp_path / "assets"
    storage = LocalOriginalStorage(originals, assets)
    storage.put("originals/papers/p1/original.pdf", b"pdf")
    storage.put("assets/papers/p1/figure.png", b"png")
    assert storage.path_for("originals/papers/p1/original.pdf").is_relative_to(originals)
    assert storage.path_for("assets/papers/p1/figure.png").is_relative_to(assets)
    assert not (assets / "papers/p1/original.pdf").exists()
    assert not (originals / "papers/p1/figure.png").exists()


class FakeObjectStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[dict] = []

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        key = kwargs["Key"]
        if key in self.objects:
            raise FakeS3Error("PreconditionFailed")
        self.objects[key] = kwargs["Body"]

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise FakeS3Error("NoSuchKey")
        return {"Body": BytesIO(self.objects[Key])}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop(Key, None)


class FakeS3Error(Exception):
    def __init__(self, code: str):
        self.response = {"Error": {"Code": code}}
        super().__init__(code)


def test_s3_compatible_storage_is_immutable_and_uses_disposable_cache(tmp_path):
    client = FakeObjectStore()
    storage = S3CompatibleOriginalStorage(
        bucket="paperpilot",
        endpoint_url="https://example.invalid",
        access_key_id="key",
        secret_access_key="secret",
        prefix="research",
        cache_root=tmp_path / "cache",
        client=client,
    )
    storage.put("originals/papers/p1/original.pdf", b"pdf")
    assert client.put_calls[0]["Key"] == "research/originals/papers/p1/original.pdf"
    assert client.put_calls[0]["IfNoneMatch"] == "*"
    with pytest.raises(ImmutableObjectExists):
        storage.put("originals/papers/p1/original.pdf", b"replacement")
    assert storage.path_for("originals/papers/p1/original.pdf").read_bytes() == b"pdf"
    storage.delete("originals/papers/p1/original.pdf")
    with pytest.raises(FileNotFoundError):
        storage.path_for("originals/papers/p1/original.pdf")
