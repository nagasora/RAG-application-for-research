import pytest

from app.storage import ImmutableObjectExists, LocalOriginalStorage, StorageError


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
