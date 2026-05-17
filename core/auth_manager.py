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
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# 用户数据根目录
USERS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "users")
USERS_DB = os.path.join(USERS_DIR, "users.json")

# PBKDF2 参数
PBKDF2_ITERATIONS = 600000
PBKDF2_LENGTH = 64  # 输出 64 字节: 前 32 → auth_hash, 后 32 → enc_key


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
        os.makedirs(USERS_DIR, exist_ok=True)
        with open(USERS_DB, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

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
        users[username] = {
            "salt": base64.b16encode(salt).decode(),
            "auth_hash": auth_hash,
        }
        AuthManager._save_users(users)

        # 创建用户数据目录结构
        user_dir = AuthManager.get_user_dir(username)
        os.makedirs(os.path.join(user_dir, "conversations"), exist_ok=True)
        os.makedirs(os.path.join(user_dir, "bookshelf"), exist_ok=True)

        return enc_key

    @staticmethod
    def authenticate(username: str, password: str) -> tuple[bool, bytes | None]:
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
        return True, enc_key

    @staticmethod
    def get_user_dir(username: str) -> str:
        """获取用户数据目录路径"""
        return os.path.join(USERS_DIR, username)

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
