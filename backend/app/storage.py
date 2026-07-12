from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from typing import Protocol


class StorageError(Exception):
    pass


class ImmutableObjectExists(StorageError):
    pass


class OriginalStorage(Protocol):
    def put(self, key: str, content: bytes) -> None: ...
    def path_for(self, key: str) -> Path: ...
    def delete(self, key: str) -> None: ...


class LocalOriginalStorage:
    """Immutable storage split between original (read-mostly) and derived asset roots."""

    _SAFE_PART = re.compile(r"^[A-Za-z0-9._-]+$")

    def __init__(self, original_root: Path, asset_root: Path | None = None):
        self.original_root = original_root.resolve()
        self.asset_root = (asset_root or original_root).resolve()
        self.root = self.original_root  # rolling compatibility for callers inspecting root
        self.original_root.mkdir(parents=True, exist_ok=True)
        self.asset_root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        pure = PurePosixPath(key)
        if pure.is_absolute() or not pure.parts or any(
            part in {"", ".", ".."} or not self._SAFE_PART.fullmatch(part)
            for part in pure.parts
        ):
            raise StorageError("invalid storage key")
        parts = list(pure.parts)
        if parts[0] == "originals":
            root, parts = self.original_root, parts[1:]
        elif parts[0] == "assets":
            root, parts = self.asset_root, parts[1:]
        elif "assets" in parts:
            root = self.asset_root  # legacy papers/{id}/assets/... keys
        else:
            root = self.original_root  # legacy papers/{id}/original... keys
        if not parts:
            raise StorageError("invalid storage key")
        target = root.joinpath(*parts).resolve()
        if target == root or root not in target.parents:
            raise StorageError("storage key escapes root")
        return target

    def put(self, key: str, content: bytes) -> None:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as exc:
            raise ImmutableObjectExists(key) from exc
        except Exception:
            target.unlink(missing_ok=True)
            raise

    def path_for(self, key: str) -> Path:
        target = self._resolve(key)
        if not target.is_file():
            raise FileNotFoundError(key)
        return target

    def delete(self, key: str) -> None:
        target = self._resolve(key)
        target.unlink(missing_ok=True)
