"""Unified encrypted/plain storage primitives.

All callers use logical paths without the ``.enc`` suffix.  The storage
implementation keeps the existing Fernet convention when an encryption key is
available and provides atomic writes for both encrypted and plain data.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path


class StorageError(RuntimeError):
    """Raised when a storage operation cannot be completed safely."""


class EncryptedStorage:
    def __init__(self, root: str, crypto=None, enc_key: bytes | None = None) -> None:
        self.root = os.path.abspath(root)
        self.crypto = crypto
        self.enc_key = enc_key
        os.makedirs(self.root, exist_ok=True)

    @property
    def encrypted(self) -> bool:
        return self.enc_key is not None

    def resolve(self, relative_path: str) -> str:
        clean = str(relative_path or "").replace("\\", "/").strip("/")
        if not clean or clean == ".":
            return self.root
        target = os.path.abspath(os.path.join(self.root, *clean.split("/")))
        if os.path.commonpath([self.root, target]) != self.root:
            raise StorageError(f"路径越出工作区范围: {relative_path}")
        return target

    def actual_path(self, relative_path: str) -> str:
        path = self.resolve(relative_path)
        return path + ".enc" if self.encrypted else path

    def exists(self, relative_path: str) -> bool:
        return os.path.exists(self.actual_path(relative_path))

    def read_text(self, relative_path: str, default: str | None = None) -> str | None:
        actual = self.actual_path(relative_path)
        if not os.path.exists(actual):
            return default
        if self.encrypted:
            if self.crypto is None:
                raise StorageError("加密存储缺少 crypto 实现")
            return self.crypto.decrypt_text(self.enc_key, actual)
        with open(actual, "r", encoding="utf-8") as handle:
            return handle.read()

    def write_text(self, relative_path: str, text: str, *, atomic: bool = True) -> str:
        actual = self.actual_path(relative_path)
        os.makedirs(os.path.dirname(actual), exist_ok=True)
        if not atomic:
            self._write_text_actual(relative_path, text)
            return actual
        temp_relative = f"{relative_path}.tmp-{uuid.uuid4().hex}"
        temp_actual = self.actual_path(temp_relative)
        try:
            self._write_text_actual(temp_relative, text)
            os.replace(temp_actual, actual)
        finally:
            if os.path.exists(temp_actual):
                os.remove(temp_actual)
        return actual

    def _write_text_actual(self, relative_path: str, text: str) -> None:
        actual = self.actual_path(relative_path)
        os.makedirs(os.path.dirname(actual), exist_ok=True)
        if self.encrypted:
            if self.crypto is None:
                raise StorageError("加密存储缺少 crypto 实现")
            self.crypto.encrypt_text(self.enc_key, actual, text)
            return
        with open(actual, "w", encoding="utf-8") as handle:
            handle.write(text)

    def read_json(self, relative_path: str, default=None):
        text = self.read_text(relative_path)
        if text is None:
            return default
        return json.loads(text)

    def write_json(self, relative_path: str, data, *, atomic: bool = True) -> str:
        return self.write_text(
            relative_path,
            json.dumps(data, ensure_ascii=False, indent=2),
            atomic=atomic,
        )

    def delete(self, relative_path: str) -> bool:
        actual = self.actual_path(relative_path)
        if not os.path.exists(actual):
            return False
        if os.path.isdir(actual):
            shutil.rmtree(actual)
        else:
            os.remove(actual)
        return True

    def copy(self, source: str, destination: str) -> str:
        text = self.read_text(source)
        if text is None:
            raise FileNotFoundError(source)
        return self.write_text(destination, text)

    def backup(self, relative_path: str, reason: str = "migration") -> str:
        actual = self.actual_path(relative_path)
        if not os.path.exists(actual):
            return ""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = f"{actual}.backup-{reason}-{stamp}"
        shutil.copy2(actual, backup_path)
        return backup_path

    def checksum(self, relative_path: str) -> str:
        text = self.read_text(relative_path)
        if text is None:
            raise FileNotFoundError(relative_path)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def list_files(self, prefix: str = "") -> list[str]:
        base = self.resolve(prefix)
        if not os.path.isdir(base):
            return []
        result: list[str] = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [
                name for name in dirs
                if not name.startswith(".tmp-") and ".backup-" not in name
            ]
            for name in files:
                if ".backup-" in name or ".tmp-" in name:
                    continue
                logical_name = name[:-4] if self.encrypted and name.endswith(".enc") else name
                if self.encrypted and not name.endswith(".enc"):
                    continue
                actual = os.path.join(root, logical_name)
                relative = os.path.relpath(actual, self.root).replace("\\", "/")
                result.append(relative)
        return sorted(set(result))

    def stat(self, relative_path: str) -> os.stat_result:
        return os.stat(self.actual_path(relative_path))

    def make_read_only_backup(self, relative_path: str, reason: str = "migration") -> str:
        backup = self.backup(relative_path, reason)
        if backup:
            try:
                Path(backup).chmod(0o444)
            except OSError:
                pass
        return backup
