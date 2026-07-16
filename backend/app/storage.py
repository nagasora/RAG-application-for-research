from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Protocol


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


class S3CompatibleOriginalStorage:
    """Immutable storage backed by an S3-compatible object store.

    Cloudflare R2 implements the S3 API, so this class deliberately does not
    depend on R2-specific SDK behaviour.  ``path_for`` downloads an object to a
    private cache because the existing extraction and file-serving boundaries
    work with ``Path`` objects.  The cache is only a disposable read-through
    copy; the object store remains the source of truth.
    """

    _SAFE_PART = LocalOriginalStorage._SAFE_PART

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        region_name: str = "auto",
        prefix: str = "",
        cache_root: Path | None = None,
        client: Any | None = None,
    ):
        if not bucket or not endpoint_url or not access_key_id or not secret_access_key:
            raise StorageError("S3-compatible storage requires bucket, endpoint, and credentials")
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.prefix = self._normalise_prefix(prefix)
        self.cache_root = (cache_root or Path(tempfile.gettempdir()) / "paperpilot-object-cache").resolve()
        self.cache_root.mkdir(parents=True, exist_ok=True)
        if client is not None:
            self.client = client
            return
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - guarded by deployment dependency
            raise StorageError("boto3 is required for S3-compatible storage") from exc
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region_name,
        )

    @classmethod
    def _validate_key(cls, key: str) -> PurePosixPath:
        pure = PurePosixPath(key)
        if pure.is_absolute() or not pure.parts or any(
            part in {"", ".", ".."} or not cls._SAFE_PART.fullmatch(part)
            for part in pure.parts
        ):
            raise StorageError("invalid storage key")
        return pure

    @classmethod
    def _normalise_prefix(cls, prefix: str) -> str:
        if not prefix:
            return ""
        pure = cls._validate_key(prefix.strip("/"))
        return "/".join(pure.parts)

    def _object_key(self, key: str) -> str:
        pure = self._validate_key(key)
        value = "/".join(pure.parts)
        return f"{self.prefix}/{value}" if self.prefix else value

    def _cache_path(self, key: str) -> Path:
        pure = self._validate_key(key)
        path = self.cache_root.joinpath(*pure.parts).resolve()
        if path == self.cache_root or self.cache_root not in path.parents:
            raise StorageError("storage key escapes cache root")
        return path

    @staticmethod
    def _error_code(exc: Exception) -> str:
        response = getattr(exc, "response", None)
        error = response.get("Error", {}) if isinstance(response, dict) else {}
        return str(error.get("Code", ""))

    def put(self, key: str, content: bytes) -> None:
        object_key = self._object_key(key)
        try:
            # R2 and current S3 support this conditional write.  It closes the
            # head-then-put race and preserves the app's immutable-object rule.
            self.client.put_object(
                Bucket=self.bucket,
                Key=object_key,
                Body=content,
                IfNoneMatch="*",
            )
        except Exception as exc:
            if self._error_code(exc) in {"PreconditionFailed", "ConditionalRequestConflict", "412"}:
                raise ImmutableObjectExists(key) from exc
            raise

    def path_for(self, key: str) -> Path:
        target = self._cache_path(key)
        if target.is_file():
            return target
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._object_key(key))
            body = response["Body"].read()
        except Exception as exc:
            if self._error_code(exc) in {"NoSuchKey", "NotFound", "404"}:
                raise FileNotFoundError(key) from exc
            raise
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f"{target.suffix}.part")
        try:
            with temporary.open("xb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except FileExistsError:
            # A concurrent request has already populated the same cache item.
            pass
        finally:
            temporary.unlink(missing_ok=True)
        if not target.is_file():
            raise FileNotFoundError(key)
        return target

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._object_key(key))
        self._cache_path(key).unlink(missing_ok=True)


def storage_from_environment(*, base_dir: Path | None = None) -> OriginalStorage:
    """Build the configured durable storage, defaulting to local files.

    ``PAPER_STORAGE_BACKEND`` accepts ``local`` (default), ``r2``, or ``s3``.
    R2 variables take precedence over generic S3 aliases, allowing the same
    application configuration to work with another S3-compatible provider.
    """
    backend = os.getenv("PAPER_STORAGE_BACKEND", "local").strip().lower()
    if backend in {"r2", "s3"}:
        endpoint = os.getenv("R2_ENDPOINT_URL") or os.getenv("S3_ENDPOINT_URL")
        account_id = os.getenv("R2_ACCOUNT_ID")
        if not endpoint and account_id:
            endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
        return S3CompatibleOriginalStorage(
            bucket=os.getenv("R2_BUCKET") or os.getenv("S3_BUCKET", ""),
            endpoint_url=endpoint or "",
            access_key_id=os.getenv("R2_ACCESS_KEY_ID") or os.getenv("S3_ACCESS_KEY_ID", ""),
            secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY") or os.getenv("S3_SECRET_ACCESS_KEY", ""),
            region_name=os.getenv("R2_REGION") or os.getenv("S3_REGION", "auto"),
            prefix=os.getenv("R2_PREFIX") or os.getenv("S3_PREFIX", ""),
            cache_root=Path(os.getenv("PAPER_STORAGE_CACHE_DIR", tempfile.gettempdir())) / "paperpilot-object-cache",
        )
    if backend != "local":
        raise StorageError("PAPER_STORAGE_BACKEND must be local, r2, or s3")
    legacy = os.getenv("PAPER_STORAGE_DIR")
    original = Path(os.getenv("PAPER_ORIGINAL_STORAGE_DIR", legacy or "./data/originals"))
    assets = Path(os.getenv("PAPER_ASSET_STORAGE_DIR", "./data/assets" if not legacy else legacy))
    root = base_dir or Path.cwd()
    original_root = original if original.is_absolute() else root / original
    asset_root = assets if assets.is_absolute() else root / assets
    return LocalOriginalStorage(original_root, asset_root)
