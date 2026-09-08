"""Local filesystem object storage (Phase 4). No DB access."""

from __future__ import annotations

import hashlib
from pathlib import Path

from backend.infrastructure.config import get_settings
from backend.tools.errors import ToolError


class ObjectStorage:
    def __init__(self, root: str | Path | None = None) -> None:
        settings = get_settings()
        self.root = Path(root or settings.storage_root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, storage_key: str, data: bytes) -> str:
        path = self._path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = path.read_bytes()
            if existing != data:
                raise ToolError(
                    "storage_key already exists with different bytes",
                    code="STORAGE_CONFLICT",
                )
            return storage_key
        path.write_bytes(data)
        return storage_key

    def get_bytes(self, storage_key: str) -> bytes:
        path = self._path(storage_key)
        if not path.is_file():
            raise ToolError(f"object not found: {storage_key}", code="STORAGE_NOT_FOUND")
        return path.read_bytes()

    def exists(self, storage_key: str) -> bool:
        return self._path(storage_key).is_file()

    def content_hash(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _path(self, storage_key: str) -> Path:
        # Prevent path traversal
        key = storage_key.replace("\\", "/").lstrip("/")
        if ".." in key.split("/"):
            raise ToolError("invalid storage_key", code="STORAGE_INVALID_KEY")
        return self.root / key
