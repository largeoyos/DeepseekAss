"""Revocable local automation grants for the headless control interface."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken

from core.auth_manager import AuthManager


class ControlAuthError(PermissionError):
    pass


@dataclass(frozen=True)
class ControlGrant:
    grant_id: str
    username: str
    name: str
    scopes: tuple[str, ...]
    created_at: str
    expires_at: str
    enc_key: bytes

    def public_dict(self) -> dict:
        data = asdict(self)
        data.pop("enc_key", None)
        data["scopes"] = list(self.scopes)
        return data


class ControlGrantStore:
    TOKEN_PREFIX = "dsa"
    VALID_SCOPES = {"read", "propose", "generate"}

    @staticmethod
    def _registry_path(username: str) -> str:
        return os.path.join(AuthManager.get_user_dir(username), ".deepseekass", "control_grants.json")

    @classmethod
    def _load(cls, username: str) -> dict:
        path = cls._registry_path(username)
        if not os.path.exists(path):
            return {"schema_version": 1, "grants": []}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ControlAuthError(f"自动化授权文件无法读取: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("grants", []), list):
            raise ControlAuthError("自动化授权文件格式无效")
        return data

    @classmethod
    def _save(cls, username: str, data: dict) -> None:
        path = cls._registry_path(username)
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix="control_grants.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    @staticmethod
    def _secret_bytes(secret: str) -> bytes:
        return secret.encode("utf-8")

    @classmethod
    def _digest(cls, secret: str, salt: bytes) -> str:
        return hashlib.sha256(salt + cls._secret_bytes(secret)).hexdigest()

    @classmethod
    def _wrapping_key(cls, secret: str, salt: bytes) -> bytes:
        digest = hashlib.sha256(b"deepseekass-control-wrap\0" + salt + cls._secret_bytes(secret)).digest()
        return base64.urlsafe_b64encode(digest)

    @classmethod
    def create(
        cls,
        username: str,
        password: str,
        *,
        name: str,
        scopes: set[str] | None = None,
        expires_days: int = 90,
    ) -> tuple[str, dict]:
        ok, enc_key = AuthManager.authenticate(username, password)
        if not ok or enc_key is None:
            raise ControlAuthError("用户名或密码错误")
        selected = set(scopes or {"read", "propose"})
        if not selected or not selected.issubset(cls.VALID_SCOPES):
            raise ControlAuthError("授权范围只能包含 read、propose 和 generate")
        if expires_days < 1 or expires_days > 3650:
            raise ControlAuthError("授权有效期必须在 1 到 3650 天之间")

        grant_id = uuid.uuid4().hex[:16]
        secret = secrets.token_urlsafe(32)
        salt = secrets.token_bytes(16)
        token = f"{cls.TOKEN_PREFIX}_{grant_id}_{secret}"
        now = datetime.now().astimezone()
        record = {
            "grant_id": grant_id,
            "name": str(name or "AI control").strip()[:120] or "AI control",
            "scopes": sorted(selected),
            "created_at": now.isoformat(timespec="seconds"),
            "expires_at": (now + timedelta(days=expires_days)).isoformat(timespec="seconds"),
            "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
            "token_digest": cls._digest(secret, salt),
            "wrapped_enc_key": Fernet(cls._wrapping_key(secret, salt)).encrypt(enc_key).decode("ascii"),
        }
        data = cls._load(username)
        data.setdefault("grants", []).append(record)
        cls._save(username, data)
        public = {key: value for key, value in record.items() if key not in {"salt", "token_digest", "wrapped_enc_key"}}
        return token, public

    @classmethod
    def resolve(cls, username: str, token: str) -> ControlGrant:
        parts = str(token or "").split("_", 2)
        if len(parts) != 3 or parts[0] != cls.TOKEN_PREFIX:
            raise ControlAuthError("自动化令牌格式无效")
        _prefix, grant_id, secret = parts
        record = next((item for item in cls._load(username).get("grants", []) if item.get("grant_id") == grant_id), None)
        if not isinstance(record, dict):
            raise ControlAuthError("自动化令牌不存在或已撤销")
        try:
            expires_at = datetime.fromisoformat(str(record["expires_at"]))
            if expires_at.tzinfo is None:
                expires_at = expires_at.astimezone()
            if datetime.now().astimezone() >= expires_at:
                raise ControlAuthError("自动化令牌已过期")
            salt = base64.urlsafe_b64decode(str(record["salt"]).encode("ascii"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ControlAuthError("自动化授权记录已损坏") from exc
        actual = cls._digest(secret, salt)
        if not hmac.compare_digest(actual, str(record.get("token_digest", ""))):
            raise ControlAuthError("自动化令牌无效")
        try:
            enc_key = Fernet(cls._wrapping_key(secret, salt)).decrypt(str(record["wrapped_enc_key"]).encode("ascii"))
        except (InvalidToken, KeyError, TypeError, ValueError) as exc:
            raise ControlAuthError("自动化令牌无法解锁用户数据") from exc
        scopes = tuple(str(item) for item in record.get("scopes", []) if str(item) in cls.VALID_SCOPES)
        return ControlGrant(
            grant_id=grant_id,
            username=username,
            name=str(record.get("name") or "AI control"),
            scopes=scopes,
            created_at=str(record.get("created_at") or ""),
            expires_at=str(record.get("expires_at") or ""),
            enc_key=enc_key,
        )

    @classmethod
    def list(cls, username: str, password: str) -> list[dict]:
        ok, _key = AuthManager.authenticate(username, password)
        if not ok:
            raise ControlAuthError("用户名或密码错误")
        result = []
        for record in cls._load(username).get("grants", []):
            result.append({key: value for key, value in record.items() if key not in {"salt", "token_digest", "wrapped_enc_key"}})
        return sorted(result, key=lambda item: str(item.get("created_at", "")), reverse=True)

    @classmethod
    def revoke(cls, username: str, password: str, grant_id: str) -> bool:
        ok, _key = AuthManager.authenticate(username, password)
        if not ok:
            raise ControlAuthError("用户名或密码错误")
        data = cls._load(username)
        before = len(data.get("grants", []))
        data["grants"] = [item for item in data.get("grants", []) if item.get("grant_id") != grant_id]
        if len(data["grants"]) == before:
            return False
        cls._save(username, data)
        return True
