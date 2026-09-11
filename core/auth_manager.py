"""
用户认证与加密管理模块
负责：
- 用户注册与登录（密码哈希验证）
- 密钥派生（PBKDF2 → Fernet）
- 文件加密/解密（使用 cryptography.fernet）
"""
import base64
import json
import os
import shutil
import threading
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from core.data_paths import DATA_ROOT
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# 用户数据根目录
USERS_DIR = os.path.join(DATA_ROOT, "users")
USERS_DB = os.path.join(USERS_DIR, "users.json")

# PBKDF2 参数
PBKDF2_ITERATIONS = 600000
PBKDF2_LENGTH = 64  # 输出 64 字节: 前 32 → auth_hash, 后 32 → enc_key
_AUTH_LOCK = threading.RLock()


class AuthError(Exception):
    """认证相关错误"""
    pass


class AuthManager:
    """用户认证与加密管理"""

    # ========== 用户管理 ==========

    @staticmethod
    def _load_users() -> dict:
        """加载用户数据库"""
        if not os.path.exists(USERS_DB):
            return {}
        try:
            with open(USERS_DB, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    @staticmethod
    def _save_users(users: dict) -> None:
        """保存用户数据库"""
        AuthManager._atomic_write_bytes(
            USERS_DB, json.dumps(users, ensure_ascii=False, indent=2).encode("utf-8")
        )

    @staticmethod
    def _atomic_write_bytes(path: str, data: bytes) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = path + ".tmp-" + uuid.uuid4().hex
        try:
            with open(temporary, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)

    @staticmethod
    def _password_transaction_dir(dir_id: str) -> str:
        if not dir_id or any(c not in "0123456789abcdef" for c in dir_id):
            raise AuthError("用户数据目录标识无效")
        root = os.path.realpath(USERS_DIR)
        target = os.path.realpath(os.path.join(root, ".password-change-" + dir_id))
        if os.path.normcase(os.path.dirname(target)) != os.path.normcase(root):
            raise AuthError("密码迁移目录越界")
        return target

    @staticmethod
    def _recover_password_change(username: str) -> bool:
        """Recover a prepared transaction; the atomic users DB write is its commit."""
        record = AuthManager._load_users().get(username) or {}
        if not record.get("dir_id"):
            return False
        transaction = AuthManager._password_transaction_dir(record["dir_id"])
        journal_path = os.path.join(transaction, "journal.json")
        if not os.path.isfile(journal_path):
            # Staging was interrupted before any live file could be changed.
            if os.path.isdir(transaction):
                shutil.rmtree(transaction)
            return False
        with open(journal_path, encoding="utf-8") as handle:
            journal = json.load(handle)
        committed = all(record.get(key) == journal["new_record"].get(key)
                        for key in ("salt", "auth_hash"))
        if not committed and not journal.get("rolled_back"):
            if any(record.get(key) != journal["old_record"].get(key)
                   for key in ("salt", "auth_hash")):
                raise AuthError("密码迁移记录与账号不一致，已保留原密文备份")
            user_dir = os.path.realpath(AuthManager.get_user_dir(username))
            for index, relative in enumerate(journal["files"]):
                target = os.path.realpath(os.path.join(user_dir, relative))
                if os.path.commonpath([user_dir, target]) != user_dir or target == user_dir:
                    raise AuthError("密码迁移文件路径越界")
                backup = os.path.join(transaction, f"{index}.old")
                with open(backup, "rb") as handle:
                    AuthManager._atomic_write_bytes(target, handle.read())
            # If cleanup itself is interrupted after deleting some backups,
            # future logins must not try restoring those backups again.
            journal["rolled_back"] = True
            AuthManager._atomic_write_bytes(journal_path, json.dumps(journal).encode("utf-8"))
        shutil.rmtree(transaction)
        return committed

    @staticmethod
    def user_exists(username: str) -> bool:
        """检查用户是否存在"""
        return username in AuthManager._load_users()

    @staticmethod
    def register(username: str, password: str) -> bytes:
        """
        注册新用户

        Args:
            username: 用户名
            password: 密码

        Returns:
            enc_key: Fernet 加密密钥（bytes，用于后续的数据加解密）

        Raises:
            AuthError: 用户已存在或参数无效
        """
        if not username.strip():
            raise AuthError("用户名不能为空")
        if not password:
            raise AuthError("密码不能为空")
        if AuthManager.user_exists(username):
            raise AuthError(f"用户 '{username}' 已存在")

        salt = os.urandom(16)
        full_key = AuthManager._derive_full_key(password, salt)

        auth_hash = base64.urlsafe_b64encode(full_key[:32]).decode()
        enc_key = base64.urlsafe_b64encode(full_key[32:])

        users = AuthManager._load_users()
        dir_id = uuid.uuid4().hex[:12]
        users[username] = {
            "salt": base64.b16encode(salt).decode(),
            "auth_hash": auth_hash,
            "dir_id": dir_id,
        }
        AuthManager._save_users(users)

        # 创建用户数据目录结构
        user_dir = AuthManager.get_user_dir(username)
        os.makedirs(os.path.join(user_dir, "conversations"), exist_ok=True)
        os.makedirs(os.path.join(user_dir, "bookshelf"), exist_ok=True)

        return enc_key

    @staticmethod
    def authenticate(username: str, password: str) -> tuple[bool, bytes | None]:
        with _AUTH_LOCK:
            try:
                AuthManager._recover_password_change(username)
            except Exception as exc:
                raise AuthError(f"密码迁移恢复失败，原密文备份已保留：{exc}") from exc
            return AuthManager._authenticate(username, password)

    @staticmethod
    def _authenticate(username: str, password: str) -> tuple[bool, bytes | None]:
        """
        验证用户密码

        Args:
            username: 用户名
            password: 密码

        Returns:
            (成功?, enc_key 或 None)
        """
        users = AuthManager._load_users()
        record = users.get(username)
        if record is None:
            return False, None

        salt = base64.b16decode(record["salt"].upper())
        stored_hash = record["auth_hash"]

        full_key = AuthManager._derive_full_key(password, salt)
        computed_hash = base64.urlsafe_b64encode(full_key[:32]).decode()

        if computed_hash != stored_hash:
            return False, None

        enc_key = base64.urlsafe_b64encode(full_key[32:])

        # 旧用户迁移：分配 dir_id，将用户数据移到 UUID 目录
        if "dir_id" not in record:
            dir_id = uuid.uuid4().hex[:12]
            old_dir = os.path.join(USERS_DIR, AuthManager._safe_name(username))
            new_dir = os.path.join(USERS_DIR, dir_id)
            if os.path.isdir(old_dir) and old_dir != new_dir:
                os.makedirs(os.path.dirname(new_dir), exist_ok=True)
                shutil.move(old_dir, new_dir)
            record["dir_id"] = dir_id
            AuthManager._save_users(users)

        return True, enc_key

    @staticmethod
    def change_password(username: str, old_password: str, new_password: str) -> bytes:
        with _AUTH_LOCK:
            return AuthManager._change_password(username, old_password, new_password)

    @staticmethod
    def _change_password(username: str, old_password: str, new_password: str) -> bytes:
        """
        Change a user's password and re-encrypt all user data with the new key.

        The users database is updated only after every encrypted file in the
        user's directory has been successfully rewritten.
        """
        if not new_password:
            raise AuthError("新密码不能为空")
        ok, old_key = AuthManager.authenticate(username, old_password)
        if not ok or old_key is None:
            raise AuthError("旧密码错误")

        users = AuthManager._load_users()
        record = users.get(username)
        if record is None:
            raise AuthError("用户不存在")

        new_salt = os.urandom(16)
        new_full_key = AuthManager._derive_full_key(new_password, new_salt)
        new_auth_hash = base64.urlsafe_b64encode(new_full_key[:32]).decode()
        new_enc_key = base64.urlsafe_b64encode(new_full_key[32:])

        user_dir = AuthManager.get_user_dir(username)
        encrypted_files: list[str] = []
        for root, _, files in os.walk(user_dir):
            for fname in files:
                if fname.endswith(".enc"):
                    encrypted_files.append(os.path.join(root, fname))

        transaction = AuthManager._password_transaction_dir(record["dir_id"])
        os.makedirs(transaction)
        new_record = {**record, "salt": base64.b16encode(new_salt).decode(),
                      "auth_hash": new_auth_hash}
        journal = {"old_record": record, "new_record": new_record,
                   "files": [os.path.relpath(path, user_dir) for path in encrypted_files]}
        journal_path = os.path.join(transaction, "journal.json")
        try:
            for index, path in enumerate(encrypted_files):
                with open(path, "rb") as f:
                    raw = f.read()
                plaintext = AuthManager.decrypt(old_key, raw)
                AuthManager._atomic_write_bytes(os.path.join(transaction, f"{index}.old"), raw)
                AuthManager._atomic_write_bytes(os.path.join(transaction, f"{index}.new"),
                                                AuthManager.encrypt(new_enc_key, plaintext))
            AuthManager._atomic_write_bytes(journal_path, json.dumps(journal).encode("utf-8"))
            for index, path in enumerate(encrypted_files):
                os.replace(os.path.join(transaction, f"{index}.new"), path)
            # Reload so registrations completed while staging are retained.
            users = AuthManager._load_users()
            users[username] = new_record
            AuthManager._save_users(users)
        except Exception as exc:
            try:
                if AuthManager._recover_password_change(username):
                    return new_enc_key
            except Exception as recovery_exc:
                raise AuthError(f"密码迁移中断，恢复记录与原密文已保留；重新登录可重试恢复：{recovery_exc}") from exc
            raise AuthError(f"数据重加密失败，密码未修改：{exc}") from exc
        # Cleanup failure does not undo a committed password change. A later
        # authentication recognizes the committed journal and retries cleanup.
        try:
            AuthManager._recover_password_change(username)
        except OSError:
            pass
        return new_enc_key

    @staticmethod
    def get_user_dir(username: str) -> str:
        """获取用户数据目录路径（使用 dir_id，而非用户名原文）"""
        users = AuthManager._load_users()
        record = users.get(username)
        if record and "dir_id" in record:
            return os.path.join(USERS_DIR, record["dir_id"])
        # 兜底：旧格式用户或无 dir_id
        return os.path.join(USERS_DIR, AuthManager._safe_name(username))

    @staticmethod
    def _safe_name(name: str) -> str:
        """安全的文件/目录名"""
        return name.replace("/", "-").replace("\\", "-").replace(":", "：")

    @staticmethod
    def _derive_full_key(password: str, salt: bytes) -> bytes:
        """用 PBKDF2 派生 64 字节密钥"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=PBKDF2_LENGTH,
            salt=salt,
            iterations=PBKDF2_ITERATIONS,
        )
        return kdf.derive(password.encode("utf-8"))

    # ========== 加密/解密原语 ==========

    @staticmethod
    def encrypt(key: bytes, plaintext: bytes) -> bytes:
        """Fernet 加密"""
        f = Fernet(key)
        return f.encrypt(plaintext)

    @staticmethod
    def decrypt(key: bytes, ciphertext: bytes) -> bytes:
        """Fernet 解密"""
        f = Fernet(key)
        try:
            return f.decrypt(ciphertext)
        except InvalidToken:
            raise AuthError("数据解密失败，可能密码错误或数据已损坏")

    # ========== 文件级加密操作 ==========

    @staticmethod
    def encrypt_json(key: bytes, path: str, data: dict) -> None:
        """加密 JSON 写入文件"""
        raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        encrypted = AuthManager.encrypt(key, raw)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(encrypted)

    @staticmethod
    def decrypt_json(key: bytes, path: str) -> dict | None:
        """读取并解密 JSON 文件"""
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            encrypted = f.read()
        raw = AuthManager.decrypt(key, encrypted)
        return json.loads(raw.decode("utf-8"))

    @staticmethod
    def encrypt_text(key: bytes, path: str, text: str) -> None:
        """加密文本写入文件"""
        encrypted = AuthManager.encrypt(key, text.encode("utf-8"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(encrypted)

    @staticmethod
    def decrypt_text(key: bytes, path: str) -> str | None:
        """读取并解密文本文件"""
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            encrypted = f.read()
        raw = AuthManager.decrypt(key, encrypted)
        return raw.decode("utf-8")
