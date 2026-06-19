"""
小说管理器模块
负责：
- 书架管理（创建/列出/删除小说项目）
- 章节文件读写与版本管理（多版本保留、选择活跃版本）
- 前情提要自动生成与读取
- 小说元信息（标题、主角设定、背景故事）持久化
- 智能前情提要选取算法（长篇小说自动压缩早期章节）
"""

import json
import os
import shutil
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime

# 书架根目录（相对于项目根目录）
BOOKSHELF_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bookshelf")


def _maybe_encrypt_path(path: str) -> str:
    """如果是加密文件，添加 .enc 后缀"""
    return path + ".enc"


@dataclass
class ChapterVersionInfo:
    """章节单个版本的信息"""
    v: int = 1
    title: str = ""
    file: str = ""
    created_at: str = ""


@dataclass
class ChapterInfo:
    """章节版本组信息"""
    active: int = 1  # 活跃版本号
    versions: list[ChapterVersionInfo] = field(default_factory=list)


@dataclass
class NovelMeta:
    """单部小说的元信息"""
    title: str = ""
    author: str = "AI Assistant"
    protagonist_bio: str = ""
    background_story: str = ""
    writing_demand: str = ""
    author_plan: str = ""   # 作者规划层：主线/阶段目标/人物弧光/主题/节奏/禁写事项
    genre: str = ""        # 题材 key（对应 utils.genre_styles.GENRES）
    style_tone: str = ""   # 风格基调 key（对应 utils.genre_styles.STYLE_TONES）
    xp_mode: bool = False  # 狂野向创作模式开关
    created_at: str = ""
    updated_at: str = ""
    total_chapters: int = 0
    chapter_titles: list[str] = field(default_factory=list)
    # 章节版本管理：key = 章节编号(str), value = ChapterInfo
    chapter_versions: dict[str, dict] = field(default_factory=dict)
    # 早期章节压缩缓存：{ "compressed_early": "..." }
    compressed_early_summary: str = ""
    # 加密模式下的小说唯一标识（UUID 目录名）
    book_id: str = ""
    # 章节树元数据（schema_version 1 为旧线性版本，2 为树形兼容层）
    schema_version: int = 1
    root_chapter_id: str = ""
    active_path: list[str] = field(default_factory=list)
    chapter_nodes: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self):
        """加载时归一化：确保文本字段是 str（兼容 LLM 返回 JSON 数组/对象）。"""
        for field_name in ("protagonist_bio", "background_story", "writing_demand", "author_plan"):
            setattr(self, field_name, self._coerce_text(getattr(self, field_name)))

    @staticmethod
    def _coerce_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n".join(NovelMeta._coerce_text(item) for item in value if item is not None)
        if isinstance(value, dict):
            lines = []
            for key, item in value.items():
                text = NovelMeta._coerce_text(item).strip()
                if text:
                    lines.append(f"{key}: {text}")
            if lines:
                return "\n".join(lines)
            return json.dumps(value, ensure_ascii=False, indent=2)
        return str(value)


class NovelManager:
    """小说管理器：书架+章节+摘要+版本管理"""

    def __init__(self, bookshelf_root: str | None = None,
                 crypto=None, enc_key: bytes | None = None):
        self._bookshelf_root = bookshelf_root or BOOKSHELF_DIR
        self._crypto = crypto
        self._enc_key = enc_key
        # 书名 → UUID 目录名缓存（仅加密模式使用）
        self._book_cache: dict[str, str] | None = None
        os.makedirs(self._bookshelf_root, exist_ok=True)

    # ========== 加密文件 I/O 辅助 ==========

    def _encrypt_path(self, path: str) -> str:
        """根据是否启用加密返回实际路径"""
        if self._enc_key is None:
            return path
        return path + ".enc"

    def _read_encrypted_text(self, path: str) -> str | None:
        """读取并解密文本文件"""
        enc_path = self._encrypt_path(path)
        if not os.path.exists(enc_path):
            return None
        if self._enc_key is None:
            with open(enc_path, "r", encoding="utf-8") as f:
                return f.read()
        return self._crypto.decrypt_text(self._enc_key, enc_path)

    def _write_encrypted_text(self, path: str, text: str) -> None:
        """加密文本写入文件"""
        enc_path = self._encrypt_path(path)
        os.makedirs(os.path.dirname(enc_path), exist_ok=True)
        if self._enc_key is None:
            with open(enc_path, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            self._crypto.encrypt_text(self._enc_key, enc_path, text)

    def _read_encrypted_json(self, path: str) -> dict | None:
        """读取并解密 JSON 文件"""
        enc_path = self._encrypt_path(path)
        if not os.path.exists(enc_path):
            return None
        if self._enc_key is None:
            with open(enc_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return self._crypto.decrypt_json(self._enc_key, enc_path)

    def _write_encrypted_json(self, path: str, data: dict) -> None:
        """加密 JSON 写入文件"""
        enc_path = self._encrypt_path(path)
        os.makedirs(os.path.dirname(enc_path), exist_ok=True)
        if self._enc_key is None:
            with open(enc_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        else:
            self._crypto.encrypt_json(self._enc_key, enc_path, data)

    def _encrypted_file_exists(self, path: str) -> bool:
        """检查加密文件是否存在"""
        return os.path.exists(self._encrypt_path(path))

    # ========== 加密模式下的书名 → 目录映射 ==========

    def _rebuild_book_cache(self) -> None:
        """扫描书架目录，重建 书名→UUID 缓存（仅加密模式）"""
        self._book_cache = {}
        if not os.path.isdir(self._bookshelf_root):
            return
        for d in os.listdir(self._bookshelf_root):
            dir_path = os.path.join(self._bookshelf_root, d)
            if not os.path.isdir(dir_path):
                continue
            # 读取 meta 获取真实书名
            meta_path = os.path.join(dir_path, "meta.json")
            try:
                meta = self._read_encrypted_json(meta_path)
                if meta and "title" in meta:
                    self._book_cache[meta["title"]] = d
            except Exception:
                continue

    def _invalidate_book_cache(self) -> None:
        """清除缓存，下次读取时重建"""
        self._book_cache = None

    def _book_dir(self, title: str) -> str:
        """
        返回小说目录路径

        非加密模式：使用安全化的书名作为目录名
        加密模式：使用 UUID（新建时生成）或旧式书名（迁移数据）
        """
        if self._enc_key is None:
            safe = title.replace("/", "-").replace("\\", "-").replace(":", "：")
            return os.path.join(self._bookshelf_root, safe)

        # 加密模式：从缓存查找 UUID
        if self._book_cache is None:
            self._rebuild_book_cache()
        cached = self._book_cache.get(title)
        if cached:
            return os.path.join(self._bookshelf_root, cached)

        # 新书（尚未有 UUID）或不命中 → 用旧式书名路径作为兜底
        safe = title.replace("/", "-").replace("\\", "-").replace(":", "：")
        return os.path.join(self._bookshelf_root, safe)

    # ========== 书架操作 ==========

    def list_books(self) -> list[str]:
        """列出书架上所有小说"""
        if not os.path.isdir(self._bookshelf_root):
            return []
        if self._enc_key is None:
            # 非加密模式：直接用目录名
            return sorted(
                d for d in os.listdir(self._bookshelf_root)
                if os.path.isdir(os.path.join(self._bookshelf_root, d))
            )
        # 加密模式：通过缓存获取真实书名
        if self._book_cache is None:
            self._rebuild_book_cache()
        titles = [t for t in (self._book_cache or {}) if self._book_cache.get(t)]
        # 补充缓存中不存在的目录（旧式书名目录）
        cached_dirs = set(self._book_cache.values())
        for d in os.listdir(self._bookshelf_root):
            if d in cached_dirs:
                continue
            if os.path.isdir(os.path.join(self._bookshelf_root, d)):
                # 尝试从 meta 读取（可能旧用户未在缓存中）
                meta_path = os.path.join(self._bookshelf_root, d, "meta.json")
                try:
                    meta = self._read_encrypted_json(meta_path)
                    if meta and "title" in meta:
                        titles.append(meta["title"])
                        self._book_cache[meta["title"]] = d
                except Exception:
                    titles.append(d)  # 读不到时用目录名
        return sorted(titles)

    def create_book(self, title: str) -> str:
        """创建新小说目录，返回该书的目录路径"""
        book_id: str | None = None
        if self._enc_key is not None:
            # 加密模式：使用 UUID 作为目录名
            # 先检查缓存中是否已有该书，避免重复创建 UUID 目录
            if self._book_cache is None:
                self._rebuild_book_cache()
            cached = self._book_cache.get(title)
            if cached:
                # 已有该书，复用现有目录，只更新 meta
                book_dir = os.path.join(self._bookshelf_root, cached)
                os.makedirs(book_dir, exist_ok=True)
                meta = self.load_meta(title)
                self._save_meta(title, meta)
                return book_dir

            book_id = uuid.uuid4().hex[:12]
            book_dir = os.path.join(self._bookshelf_root, book_id)
            self._book_cache[title] = book_id
        else:
            book_dir = self._book_dir(title)
        os.makedirs(book_dir, exist_ok=True)

        meta_path = self._meta_path(title)
        if self._encrypted_file_exists(meta_path):
            # 已有 meta，不覆盖
            meta = self.load_meta(title)
        else:
            meta = NovelMeta(
                title=title,
                book_id=book_id or "",
                created_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
                updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            )
        self._save_meta(title, meta)
        # 创建空摘要文件
        summary_path = self._summary_path(title)
        if not self._encrypted_file_exists(summary_path):
            self._write_encrypted_text(summary_path, "故事刚刚开始。\n")
        return book_dir

    def delete_book(self, title: str) -> bool:
        """删除小说及其所有章节，不可恢复"""
        book_dir = self._book_dir(title)
        if not os.path.isdir(book_dir):
            return False
        try:
            shutil.rmtree(book_dir)
        except OSError as e:
            print(f"[错误] 删除小说目录失败: {e}")
            return False
        # 验证目录确实已删除
        if os.path.isdir(book_dir):
            return False
        self._invalidate_book_cache()
        return True

    def rename_book(self, old_title: str, new_title: str) -> bool:
        """重命名小说，更新 meta.json 中的 title（加密模式下不重命名目录）"""
        old_dir = self._book_dir(old_title)
        if not os.path.isdir(old_dir):
            return False

        if self._enc_key is None:
            # 非加密模式：重命名目录
            new_dir = self._book_dir(new_title)
            if os.path.isdir(new_dir):
                return False
            os.rename(old_dir, new_dir)
            meta_path = os.path.join(new_dir, "meta.json")
        else:
            # 加密模式：目录名是 UUID，不变，只更新 meta
            meta_path = os.path.join(old_dir, "meta.json")
            if self._book_cache is not None:
                self._book_cache[new_title] = self._book_cache.pop(old_title, "")
            else:
                self._invalidate_book_cache()

        enc_meta_path = meta_path + ".enc" if self._enc_key else meta_path
        if os.path.exists(enc_meta_path):
            try:
                meta = self._read_encrypted_json(meta_path)
                if meta is not None:
                    meta["title"] = new_title
                    self._write_encrypted_json(meta_path, meta)
            except Exception:
                pass
        return True

    def load_meta(self, title: str) -> NovelMeta:
        """加载小说元信息，若不存在则返回默认空 meta"""
        meta_path = self._meta_path(title)
        if not self._encrypted_file_exists(meta_path):
            return NovelMeta(title=title)
        try:
            data = self._read_encrypted_json(meta_path)
            if data is None:
                return NovelMeta(title=title)
            valid_fields = NovelMeta.__dataclass_fields__
            filtered = {k: v for k, v in data.items() if k in valid_fields}
            return NovelMeta(**filtered)
        except Exception:
            return NovelMeta(title=title)

    def save_meta(self, title: str, **kwargs) -> NovelMeta:
        """更新小说元信息（部分字段）并保存"""
        meta = self.load_meta(title)
        for key, value in kwargs.items():
            if hasattr(meta, key):
                setattr(meta, key, value)
        meta.__post_init__()
        meta.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._save_meta(title, meta)
        return meta

    def _save_meta(self, title: str, meta: NovelMeta) -> None:
        meta_path = self._meta_path(title)
        self._write_encrypted_json(meta_path, asdict(meta))

    # ========== 章节树兼容层 ==========

    @staticmethod
    def _node_id(chapter_num: int, version: int) -> str:
        return f"ch{chapter_num:04d}_v{version:03d}"

    @staticmethod
    def _virtual_root_node_id() -> str:
        return "ch0000_v000"

    def ensure_chapter_tree(self, title: str) -> NovelMeta:
        """Build tree metadata from legacy chapter_versions when needed."""
        meta = self.load_meta(title)
        changed = self._ensure_tree_meta_from_versions(meta)
        changed = self._normalize_chapter_tree(meta) or changed
        if changed:
            self._save_meta(title, meta)
        return meta

    def _normalize_chapter_tree(self, meta: NovelMeta) -> bool:
        """Repair root, parent/child links and the active path."""
        root_id = self._virtual_root_node_id()
        changed = False
        if root_id not in meta.chapter_nodes:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            meta.chapter_nodes[root_id] = {
                "id": root_id,
                "chapter_num": 0,
                "version": 0,
                "title": "故事起点",
                "file": "",
                "summary": "",
                "user_direction": "",
                "generation_params": {},
                "parent_id": None,
                "children_ids": [],
                "sibling_order": 0,
                "created_at": now,
                "updated_at": now,
                "virtual": True,
            }
            changed = True

        root = meta.chapter_nodes[root_id]
        root_defaults = {
            "chapter_num": 0,
            "version": 0,
            "title": "故事起点",
            "file": "",
            "summary": "",
            "virtual": True,
            "parent_id": None,
        }
        for key, value in root_defaults.items():
            if root.get(key) != value:
                root[key] = value
                changed = True

        valid_ids = set(meta.chapter_nodes)
        for node_id, node in meta.chapter_nodes.items():
            if node_id == root_id:
                continue
            parent_id = node.get("parent_id")
            if not parent_id or parent_id not in valid_ids or parent_id == node_id:
                node["parent_id"] = root_id
                changed = True

        expected_children: dict[str, list[str]] = {node_id: [] for node_id in valid_ids}
        for node_id, node in meta.chapter_nodes.items():
            if node_id == root_id:
                continue
            parent_id = node.get("parent_id") or root_id
            expected_children.setdefault(parent_id, []).append(node_id)
        for parent_id, node in meta.chapter_nodes.items():
            children = sorted(
                dict.fromkeys(expected_children.get(parent_id, [])),
                key=lambda nid: (
                    int(meta.chapter_nodes[nid].get("chapter_num", 0) or 0),
                    int(meta.chapter_nodes[nid].get("sibling_order", 0) or 0),
                    int(meta.chapter_nodes[nid].get("version", 0) or 0),
                ),
            )
            if node.get("children_ids", []) != children:
                node["children_ids"] = children
                changed = True

        selected_id = next(
            (nid for nid in reversed(meta.active_path) if nid in meta.chapter_nodes),
            root_id,
        )
        desired_path: list[str] = []
        cursor: str | None = selected_id
        seen: set[str] = set()
        while cursor and cursor not in seen and cursor in meta.chapter_nodes:
            seen.add(cursor)
            desired_path.append(cursor)
            cursor = meta.chapter_nodes[cursor].get("parent_id")
        desired_path.reverse()
        if not desired_path or desired_path[0] != root_id:
            desired_path = [root_id]
        if meta.active_path != desired_path:
            meta.active_path = desired_path
            changed = True
        if meta.root_chapter_id != root_id:
            meta.root_chapter_id = root_id
            changed = True
        return changed

    def get_active_generation_target(self, title: str) -> dict:
        """Return the next chapter/version and parent for the current active path."""
        meta = self.ensure_chapter_tree(title)
        root_id = self._virtual_root_node_id()
        active_nodes = [
            meta.chapter_nodes[nid]
            for nid in meta.active_path
            if nid in meta.chapter_nodes and not meta.chapter_nodes[nid].get("virtual")
        ]
        if active_nodes:
            parent = active_nodes[-1]
            chapter_num = int(parent.get("chapter_num", 0) or 0) + 1
            parent_id = parent["id"]
        else:
            chapter_num = 1
            parent_id = root_id
        return {
            "chapter_num": chapter_num,
            "version": self.get_next_version(title, chapter_num),
            "parent_id": parent_id,
        }

    def _ensure_tree_meta_from_versions(self, meta: NovelMeta) -> bool:
        if meta.schema_version >= 2 and meta.chapter_nodes:
            return False
        nodes: dict[str, dict] = {}
        active_path: list[str] = []
        previous_active_id: str | None = None
        changed = False

        for key in sorted(meta.chapter_versions, key=lambda k: int(k) if str(k).isdigit() else 0):
            info = meta.chapter_versions[key]
            try:
                chapter_num = int(key)
            except ValueError:
                continue
            active_version = info.get("active", 1)
            parent_for_chapter = previous_active_id
            for order, version_info in enumerate(info.get("versions", []), start=1):
                version = int(version_info.get("v", 1))
                node_id = self._node_id(chapter_num, version)
                nodes[node_id] = {
                    "id": node_id,
                    "chapter_num": chapter_num,
                    "version": version,
                    "title": version_info.get("title", f"第{chapter_num}章"),
                    "file": version_info.get("file", ""),
                    "summary": version_info.get("summary", ""),
                    "user_direction": "",
                    "generation_params": {},
                    "parent_id": parent_for_chapter,
                    "children_ids": [],
                    "sibling_order": order,
                    "created_at": version_info.get("created_at", ""),
                    "updated_at": version_info.get("created_at", ""),
                }
            active_id = self._node_id(chapter_num, int(active_version))
            if active_id in nodes:
                active_path.append(active_id)
                previous_active_id = active_id

        for node in nodes.values():
            parent_id = node.get("parent_id")
            if parent_id and parent_id in nodes:
                children = nodes[parent_id].setdefault("children_ids", [])
                if node["id"] not in children:
                    children.append(node["id"])

        meta.schema_version = 2
        meta.chapter_nodes = nodes
        meta.active_path = active_path
        meta.root_chapter_id = active_path[0] if active_path else ""
        changed = True
        return changed

    def list_chapter_tree_nodes(self, title: str) -> list[dict]:
        meta = self.ensure_chapter_tree(title)
        nodes = list(meta.chapter_nodes.values())
        return sorted(nodes, key=lambda n: (int(n.get("chapter_num", 0)), int(n.get("version", 0))))

    def get_active_path_nodes(self, title: str) -> list[dict]:
        meta = self.ensure_chapter_tree(title)
        return [
            meta.chapter_nodes[nid]
            for nid in meta.active_path
            if nid in meta.chapter_nodes and not meta.chapter_nodes[nid].get("virtual")
        ]

    def read_chapter_node(self, title: str, node_id: str) -> str | None:
        meta = self.ensure_chapter_tree(title)
        node = meta.chapter_nodes.get(node_id)
        if not node or node.get("virtual"):
            return None
        return self.read_chapter_version(title, int(node["chapter_num"]), int(node["version"]))

    def set_chapter_node_summary(self, title: str, chapter_num: int, version: int, summary: str) -> None:
        """将章节摘要绑定到指定章节树节点。"""
        meta = self.ensure_chapter_tree(title)
        node_id = self._node_id(chapter_num, version)
        node = meta.chapter_nodes.get(node_id)
        if not node:
            return
        node["summary"] = (summary or "").strip()
        node["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

        key = str(chapter_num)
        for version_info in meta.chapter_versions.get(key, {}).get("versions", []):
            if int(version_info.get("v", 0)) == version:
                version_info["summary"] = node["summary"]
                break

        meta.compressed_early_summary = ""
        self._save_meta(title, meta)

    def get_chapter_node_summary(self, title: str, chapter_num: int, version: int) -> str:
        """读取指定章节树节点摘要，不存在则返回空字符串。"""
        meta = self.ensure_chapter_tree(title)
        node = meta.chapter_nodes.get(self._node_id(chapter_num, version))
        if not node:
            return ""
        summary = (node.get("summary") or "").strip()
        return "" if summary.startswith("[摘要生成失败:") else summary

    def _legacy_extract_chapter_summary(self, full_summary: str, chapter_num: int) -> str:
        pattern = rf"第{chapter_num}章「.*?」摘要：(.*?)(?=\n第\d+章「|$)"
        match = re.search(pattern, full_summary or "", re.DOTALL)
        if match:
            return match.group(1).strip()
        return ""

    def _active_summary_entries(self, title: str) -> list[dict]:
        """返回活跃路径上的摘要条目，旧书缺失节点摘要时从 plot_summary.txt 回退。"""
        meta = self.ensure_chapter_tree(title)
        legacy_summary = ""
        entries = []
        for node in self.get_active_path_nodes(title):
            chapter_num = int(node.get("chapter_num", 0) or 0)
            version = int(node.get("version", 0) or 0)
            summary = (node.get("summary") or "").strip()
            if not summary:
                if not legacy_summary:
                    summary_path = self._summary_path(title)
                    legacy_summary = self._read_encrypted_text(summary_path) or ""
                summary = self._legacy_extract_chapter_summary(legacy_summary, chapter_num)
            entries.append({
                "chapter_num": chapter_num,
                "version": version,
                "title": node.get("title", f"第{chapter_num}章"),
                "summary": summary,
                "node_id": node.get("id", self._node_id(chapter_num, version)),
            })
        return entries

    def build_active_path_summary(self, title: str) -> str:
        """按当前活跃路径拼接章节节点摘要，作为剧情记忆权威来源。"""
        entries = [e for e in self._active_summary_entries(title) if e.get("summary")]
        if not entries:
            return "故事刚刚开始。"
        parts = ["# 完整前情提要（基于章节树活跃路径）\n"]
        for entry in entries:
            parts.append(
                f"\n第{entry['chapter_num']}章「{entry['title']}」摘要：{entry['summary']}\n"
            )
        return "".join(parts).strip()

    def rebuild_plot_summary_from_tree(self, title: str) -> None:
        """从章节树活跃路径摘要生成兼容 plot_summary.txt。"""
        self._write_encrypted_text(self._summary_path(title), self.build_active_path_summary(title) + "\n")

    def switch_active_node(self, title: str, node_id: str) -> bool:
        meta = self.ensure_chapter_tree(title)
        if node_id not in meta.chapter_nodes:
            return False
        path: list[str] = []
        cursor: str | None = node_id
        seen: set[str] = set()
        while cursor and cursor not in seen:
            seen.add(cursor)
            node = meta.chapter_nodes.get(cursor)
            if not node:
                break
            path.append(cursor)
            cursor = node.get("parent_id")
        path.reverse()
        meta.active_path = path
        for active_id in path:
            active = meta.chapter_nodes.get(active_id)
            if active:
                key = str(active["chapter_num"])
                if key in meta.chapter_versions:
                    meta.chapter_versions[key]["active"] = int(active["version"])
        self._normalize_chapter_tree(meta)
        meta.compressed_early_summary = ""
        self._save_meta(title, meta)
        return True

    def delete_chapter_node(self, title: str, node_id: str) -> bool:
        meta = self.ensure_chapter_tree(title)
        if node_id not in meta.chapter_nodes or meta.chapter_nodes[node_id].get("virtual"):
            return False
        to_delete: set[str] = set()

        def collect(nid: str) -> None:
            to_delete.add(nid)
            for child_id in meta.chapter_nodes.get(nid, {}).get("children_ids", []):
                collect(child_id)

        collect(node_id)
        for nid in sorted(to_delete, reverse=True):
            node = meta.chapter_nodes.get(nid)
            if node:
                self.delete_chapter_version(title, int(node["chapter_num"]), int(node["version"]))
        return True

    # ========== 章节版本管理 ==========

    def get_next_chapter_num(self, title: str) -> int:
        """根据元信息推断下一章编号"""
        meta = self.load_meta(title)
        if meta.chapter_versions:
            existing = [int(k) for k in meta.chapter_versions.keys()]
            return max(existing) + 1 if existing else 1
        return 1

    def get_next_version(self, title: str, chapter_num: int) -> int:
        """获取某章节的下一个版本号"""
        meta = self.load_meta(title)
        key = str(chapter_num)
        if key in meta.chapter_versions:
            existing_versions = meta.chapter_versions[key].get("versions", [])
            if existing_versions:
                return max(v["v"] for v in existing_versions) + 1
        return 1

    def save_chapter_version(
        self,
        title: str,
        chapter_num: int,
        chapter_title: str,
        content: str,
        version: int | None = None,
        parent_id: str | None = None,
    ) -> tuple[str, int]:
        """
        保存一章的一个版本
        
        Returns:
            (文件路径, 版本号)
        """
        book_dir = self._book_dir(title)
        os.makedirs(book_dir, exist_ok=True)

        if version is None:
            version = self.get_next_version(title, chapter_num)

        safe_title = chapter_title.replace("/", "-").replace("\\", "-").replace(":", "：")
        file_name = f"第{chapter_num}章_{safe_title}_v{version}.txt"
        file_path = os.path.join(book_dir, file_name)
        # 记录实际写入路径（可能带 .enc）
        written_path = self._encrypt_path(file_path)

        self._write_encrypted_text(file_path, content)

        # 更新元信息中的版本记录
        meta = self.load_meta(title)
        key = str(chapter_num)
        if key not in meta.chapter_versions:
            meta.chapter_versions[key] = {
                "active": version,
                "versions": [],
            }
        # 检查是否已存在此版本
        existing = [v for v in meta.chapter_versions[key]["versions"] if v["v"] == version]
        if not existing:
            meta.chapter_versions[key]["versions"].append({
                "v": version,
                "title": chapter_title,
                "file": file_name,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
        # 如果是第一个版本，自动设为活跃
        if len(meta.chapter_versions[key]["versions"]) == 1:
            meta.chapter_versions[key]["active"] = version

        # 更新总章节数
        num = int(key)
        meta.total_chapters = max(meta.total_chapters, num)
        if chapter_title not in meta.chapter_titles:
            meta.chapter_titles.append(chapter_title)

        self._ensure_tree_meta_from_versions(meta)
        self._normalize_chapter_tree(meta)
        node_id = self._node_id(chapter_num, version)
        resolved_parent_id = parent_id if parent_id in meta.chapter_nodes else self._virtual_root_node_id()
        if parent_id is None and chapter_num > 1 and meta.active_path:
            for active_id in reversed(meta.active_path):
                active_node = meta.chapter_nodes.get(active_id)
                if (
                    active_node
                    and not active_node.get("virtual")
                    and int(active_node.get("chapter_num", 0)) < chapter_num
                ):
                    resolved_parent_id = active_id
                    break
        if node_id not in meta.chapter_nodes:
            siblings = [
                n for n in meta.chapter_nodes.values()
                if int(n.get("chapter_num", 0)) == chapter_num
            ]
            meta.chapter_nodes[node_id] = {
                "id": node_id,
                "chapter_num": chapter_num,
                "version": version,
                "title": chapter_title,
                "file": file_name,
                "summary": "",
                "user_direction": "",
                "generation_params": {},
                "parent_id": resolved_parent_id,
                "children_ids": [],
                "sibling_order": len(siblings) + 1,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            if resolved_parent_id and resolved_parent_id in meta.chapter_nodes:
                children = meta.chapter_nodes[resolved_parent_id].setdefault("children_ids", [])
                if node_id not in children:
                    children.append(node_id)
            if not meta.root_chapter_id:
                meta.root_chapter_id = node_id

        if meta.chapter_versions[key]["active"] == version:
            existing_nums = {
                int(meta.chapter_nodes[nid]["chapter_num"])
                for nid in meta.active_path
                if nid in meta.chapter_nodes
            }
            if chapter_num not in existing_nums:
                meta.active_path.append(node_id)

        self._normalize_chapter_tree(meta)
        self._save_meta(title, meta)
        return written_path, version

    def set_active_version(self, title: str, chapter_num: int, version: int) -> None:
        """设置某章节的活跃版本（用于计入剧情摘要）"""
        meta = self.load_meta(title)
        key = str(chapter_num)
        if key in meta.chapter_versions:
            meta.chapter_versions[key]["active"] = version
            self._ensure_tree_meta_from_versions(meta)
            node_id = self._node_id(chapter_num, version)
            if node_id in meta.chapter_nodes:
                path = []
                cursor: str | None = node_id
                seen: set[str] = set()
                while cursor and cursor not in seen:
                    seen.add(cursor)
                    node = meta.chapter_nodes.get(cursor)
                    if not node:
                        break
                    path.append(cursor)
                    cursor = node.get("parent_id")
                meta.active_path = list(reversed(path))
                self._normalize_chapter_tree(meta)
            self._save_meta(title, meta)

    def get_active_version(self, title: str, chapter_num: int) -> int | None:
        """获取某章节的活跃版本号"""
        meta = self.load_meta(title)
        key = str(chapter_num)
        if key in meta.chapter_versions:
            return meta.chapter_versions[key].get("active")
        return None

    def get_chapter_versions(self, title: str, chapter_num: int) -> list[dict]:
        """获取某章节的所有版本信息"""
        meta = self.load_meta(title)
        key = str(chapter_num)
        if key in meta.chapter_versions:
            return meta.chapter_versions[key].get("versions", [])
        return []

    def read_chapter_version(
        self, title: str, chapter_num: int, version: int
    ) -> str | None:
        """读取某章节指定版本的内容"""
        book_dir = self._book_dir(title)
        if not os.path.isdir(book_dir):
            return None

        prefix = f"第{chapter_num}章"
        suffix = f"_v{version}.txt"
        for fname in os.listdir(book_dir):
            # 跳过 .enc 后缀进行文件名匹配
            match_name = fname[:-4] if fname.endswith(".enc") else fname
            if match_name.startswith(prefix) and match_name.endswith(suffix):
                return self._read_encrypted_text(os.path.join(book_dir, match_name))
        return None

    def read_active_chapter(self, title: str, chapter_num: int) -> str | None:
        """读取某章节的活跃版本内容"""
        active_v = self.get_active_version(title, chapter_num)
        if active_v is None:
            return None
        return self.read_chapter_version(title, chapter_num, active_v)

    def _find_file_in_dir(self, directory: str, prefix: str, suffix: str) -> str | None:
        """在目录中查找匹配前缀和后缀的文件（会检查 .enc 变体）"""
        for fname in os.listdir(directory):
            match_name = fname[:-4] if fname.endswith(".enc") else fname
            if match_name.startswith(prefix) and match_name.endswith(suffix):
                return os.path.join(directory, fname)
        return None

    def _delete_world_bible_snapshot(self, title: str, chapter_num: int, version: int) -> None:
        from core.world_bible import _chapter_world_entry_key

        bible = self.load_world_bible(title)
        key = _chapter_world_entry_key(chapter_num, version)
        if key in getattr(bible, "chapter_world_entries", {}):
            del bible.chapter_world_entries[key]
            self.save_world_bible(title, bible)

    def delete_chapter_version(
        self, title: str, chapter_num: int, version: int
    ) -> bool:
        """删除某章节的指定版本（如果只剩一个版本则整个章节删除）"""
        book_dir = self._book_dir(title)
        if not os.path.isdir(book_dir):
            return False

        # 删除文件
        prefix = f"第{chapter_num}章"
        suffix = f"_v{version}.txt"
        found = self._find_file_in_dir(book_dir, prefix, suffix)
        if not found:
            return False
        os.remove(found)

        # 更新元信息
        meta = self.load_meta(title)
        key = str(chapter_num)
        if key in meta.chapter_versions:
            old_versions = meta.chapter_versions[key]["versions"]
            meta.chapter_versions[key]["versions"] = [
                v for v in old_versions if v["v"] != version
            ]
            # 如果删除了活跃版本，自动切换到最新版本
            if meta.chapter_versions[key]["active"] == version:
                remaining = meta.chapter_versions[key]["versions"]
                if remaining:
                    meta.chapter_versions[key]["active"] = max(
                        v["v"] for v in remaining
                    )
                else:
                    # 没有版本了，删除整个章节记录
                    del meta.chapter_versions[key]
                    meta.total_chapters = max(
                        (int(k) for k in meta.chapter_versions.keys()),
                        default=0,
                    )
            self._save_meta(title, meta)

        meta = self.load_meta(title)
        if meta.chapter_nodes:
            node_id = self._node_id(chapter_num, version)
            removed = meta.chapter_nodes.get(node_id) or {}
            parent_id = removed.get("parent_id") or self._virtual_root_node_id()
            for child_id in list(removed.get("children_ids", [])):
                child = meta.chapter_nodes.get(child_id)
                if child:
                    child["parent_id"] = parent_id
            meta.chapter_nodes.pop(node_id, None)
            meta.active_path = [nid for nid in meta.active_path if nid in meta.chapter_nodes]
            self._normalize_chapter_tree(meta)
            self._save_meta(title, meta)

        self._delete_world_bible_snapshot(title, chapter_num, version)
        return True

    def delete_chapter(self, title: str, chapter_num: int) -> bool:
        """删除某章节的所有版本"""
        book_dir = self._book_dir(title)
        if not os.path.isdir(book_dir):
            return False

        prefix = f"第{chapter_num}章"
        deleted = False
        for fname in list(os.listdir(book_dir)):
            match_name = fname[:-4] if fname.endswith(".enc") else fname
            if match_name.startswith(prefix) and match_name.endswith(".txt"):
                os.remove(os.path.join(book_dir, fname))
                deleted = True

        if deleted:
            meta = self.load_meta(title)
            key = str(chapter_num)
            deleted_versions = [
                int(item.get("v", 0) or 0)
                for item in meta.chapter_versions.get(key, {}).get("versions", [])
            ]
            meta.chapter_versions.pop(key, None)
            meta.total_chapters = max(
                (int(k) for k in meta.chapter_versions.keys()),
                default=0,
            )
            removed_ids = {
                node_id
                for node_id, node in meta.chapter_nodes.items()
                if int(node.get("chapter_num", 0) or 0) == chapter_num
            }
            for node_id in removed_ids:
                removed = meta.chapter_nodes.get(node_id) or {}
                parent_id = removed.get("parent_id") or self._virtual_root_node_id()
                for child_id in removed.get("children_ids", []):
                    child = meta.chapter_nodes.get(child_id)
                    if child and child_id not in removed_ids:
                        child["parent_id"] = parent_id
                meta.chapter_nodes.pop(node_id, None)
            meta.active_path = [nid for nid in meta.active_path if nid in meta.chapter_nodes]
            self._normalize_chapter_tree(meta)
            self._save_meta(title, meta)
            for version in deleted_versions:
                self._delete_world_bible_snapshot(title, chapter_num, version)

        return deleted

    def list_chapters(self, title: str) -> list[dict]:
        """列出某小说的所有章节及版本信息"""
        meta = self.load_meta(title)
        chapters = []
        for key, info in meta.chapter_versions.items():
            try:
                num = int(key)
            except ValueError:
                continue
            versions = info.get("versions", [])
            active_v = info.get("active", 1)
            chapters.append({
                "num": num,
                "active_version": active_v,
                "version_count": len(versions),
                "versions": versions,
                "title": versions[0]["title"] if versions else "",
            })
        chapters.sort(key=lambda c: c["num"])
        return chapters

    # ========== 摘要（剧情记忆）操作 ==========

    def load_summary(self, title: str) -> str:
        """加载小说前情提要。章节树节点摘要为主，plot_summary.txt 仅作旧数据回退。"""
        tree_summary = self.build_active_path_summary(title)
        if tree_summary and tree_summary != "故事刚刚开始。":
            return tree_summary.strip()
        summary_path = self._summary_path(title)
        text = self._read_encrypted_text(summary_path)
        if text is not None:
            return text.strip()
        return "故事刚刚开始。"

    def append_summary(
        self,
        title: str,
        chapter_num: int,
        chapter_title: str,
        summary_text: str,
    ) -> None:
        """兼容旧调用：把摘要写入当前活跃章节树节点，并刷新兼容摘要文件。"""
        active_version = self.get_active_version(title, chapter_num)
        if active_version is not None:
            self.set_chapter_node_summary(title, chapter_num, int(active_version), summary_text)
            self.rebuild_plot_summary_from_tree(title)
            return

        summary_path = self._summary_path(title)
        current = self._read_encrypted_text(summary_path) or "故事刚刚开始。\n"
        if current.strip() == "故事刚刚开始。":
            current = "故事刚刚开始。\n"
        current += f"\n第{chapter_num}章「{chapter_title}」摘要：{summary_text}\n"
        self._write_encrypted_text(summary_path, current)

    def rebuild_summary_from_active(self, client, title: str, model: str = "deepseek-v4-flash",
                                     global_user_prompt: str = "", xp_mode: bool = False) -> None:
        """
        根据当前章节树活跃路径重新生成节点摘要，并刷新兼容 plot_summary.txt。
        """
        nodes = self.get_active_path_nodes(title)
        if not nodes:
            self._write_encrypted_text(self._summary_path(title), "故事刚刚开始。\n")
            return

        meta = self.load_meta(title)
        effective_xp_mode = xp_mode or meta.xp_mode
        for node in nodes:
            chapter_num = int(node.get("chapter_num", 0) or 0)
            version = int(node.get("version", 0) or 0)
            chapter_title = node.get("title", f"第{chapter_num}章")
            content = self.read_chapter_node(title, node["id"])
            if not content:
                continue
            summary = self.generate_summary(
                client,
                content,
                chapter_num,
                chapter_title,
                model=model,
                global_user_prompt=global_user_prompt,
                xp_mode=effective_xp_mode,
            )
            if summary.strip():
                self.set_chapter_node_summary(title, chapter_num, version, summary)

        self.rebuild_plot_summary_from_tree(title)
        meta = self.load_meta(title)
        meta.compressed_early_summary = ""
        self._save_meta(title, meta)

    def _summary_context_from_entries(self, entries: list[dict], limit: int = 5) -> str:
        recent = [entry for entry in entries if entry.get("summary")][-limit:]
        return "\n".join(
            f"第{entry['chapter_num']}章：{entry['summary']}" for entry in recent
        )

    def rebuild_world_bible_from_active(
        self,
        client,
        title: str,
        model: str = "deepseek-v4-flash",
        global_user_prompt: str = "",
        xp_mode: bool = False,
        force_extract: bool = False,
        extract_missing: bool = False,
    ) -> dict:
        """
        根据所有活跃章节同步 world_bible.json。

        默认只合并章节生成/导入时保存的世界书快照，不调用模型。
        extract_missing=True 时补提取缺失快照；force_extract=True 时强制从正文重抽。
        """
        from core.world_bible import (
            WorldBible,
            _chapter_world_entry_key,
            extract_and_merge_world_bible,
            merge_extracted_world_bible_data,
        )

        existing_bible = self.load_world_bible(title)
        snapshots = dict(getattr(existing_bible, "chapter_world_entries", {}) or {})
        bible = WorldBible(chapter_world_entries=snapshots)
        nodes = self.get_active_path_nodes(title)
        report = {
            "active_chapters": len(nodes),
            "snapshot_count": 0,
            "snapshot_missing_count": 0,
            "snapshot_skipped_count": 0,
            "extracted_count": 0,
            "missing_chapters": [],
            "failed_chapters": [],
        }
        if not nodes:
            self.save_world_bible(title, bible)
            return report

        story_context_entries = []
        meta = self.load_meta(title)
        for node in nodes:
            chapter_num = int(node.get("chapter_num", 0) or 0)
            version = int(node.get("version", 0) or 0)
            story_context = self._summary_context_from_entries(story_context_entries)
            content = ""
            entry = None
            if not force_extract:
                entry = snapshots.get(
                    _chapter_world_entry_key(chapter_num, version)
                )
                if not (isinstance(entry, dict) and isinstance(entry.get("data"), dict)):
                    report["snapshot_missing_count"] += 1
            if not force_extract and isinstance(entry, dict) and isinstance(entry.get("data"), dict):
                bible = merge_extracted_world_bible_data(
                    bible,
                    entry["data"],
                    chapter_num=chapter_num,
                    chapter_version=version,
                    store_chapter_entry=True,
                    run_dedup=False,
                )
                report["snapshot_count"] += 1
            elif force_extract or extract_missing:
                content = self.read_chapter_node(title, node["id"])
                if not content:
                    report["missing_chapters"].append({
                        "chapter": chapter_num,
                        "version": version,
                    })
                    continue
                try:
                    bible = extract_and_merge_world_bible(
                        client,
                        content,
                        chapter_num,
                        bible,
                        model,
                        chapter_version=version,
                        global_user_prompt=global_user_prompt,
                        story_context=story_context,
                        background_story=meta.background_story,
                        protagonist_bio=meta.protagonist_bio,
                        writing_demand=meta.writing_demand,
                        xp_mode=xp_mode or meta.xp_mode,
                    )
                    report["extracted_count"] += 1
                except Exception as exc:
                    report["failed_chapters"].append({
                        "chapter": chapter_num,
                        "version": version,
                        "error": str(exc),
                    })
                    continue
            else:
                # 普通分支切换/删除只做本地快照合并，避免隐式触发逐章模型请求。
                report["snapshot_skipped_count"] += 1
                continue
            summary = (node.get("summary") or "").strip()
            if summary.startswith("[摘要生成失败:"):
                summary = ""
            if not summary:
                summary = self._legacy_extract_chapter_summary(self.load_summary(title), chapter_num)
            if not summary and content:
                summary = content[:300]
            story_context_entries.append({
                "chapter_num": chapter_num,
                "summary": summary,
            })

        for item in report["missing_chapters"]:
            bible.consistency_warnings.append({
                "severity": "info",
                "type": "章节内容缺失",
                "message": f"第{item['chapter']}章 v{item['version']} 缺少正文和可用快照，未参与世界书重建。",
                "related": [f"第{item['chapter']}章 v{item['version']}"],
            })
        for item in report["failed_chapters"]:
            bible.consistency_warnings.append({
                "severity": "minor",
                "type": "章节提取失败",
                "message": f"第{item['chapter']}章 v{item['version']} 世界书提取失败，未保留旧分支聚合信息。",
                "related": [f"第{item['chapter']}章 v{item['version']}"],
            })
        self.save_world_bible(title, bible)
        return report

    def extract_world_bible_for_node(
        self,
        client,
        title: str,
        node_id: str,
        model: str = "deepseek-v4-flash",
        global_user_prompt: str = "",
        xp_mode: bool = False,
    ) -> dict:
        """Refresh one chapter snapshot, then rebuild the active aggregate from snapshots."""
        from core.world_bible import WorldBible, extract_and_merge_world_bible

        meta = self.ensure_chapter_tree(title)
        node = meta.chapter_nodes.get(node_id)
        if not node or node.get("virtual"):
            raise ValueError("请选择一个正文章节节点。")
        content = self.read_chapter_node(title, node_id)
        if not content:
            raise ValueError("当前章节正文为空。")

        chapter_num = int(node.get("chapter_num", 0) or 0)
        version = int(node.get("version", 0) or 0)
        existing = self.load_world_bible(title)
        snapshot_holder = WorldBible(
            chapter_world_entries=dict(getattr(existing, "chapter_world_entries", {}) or {})
        )
        extract_and_merge_world_bible(
            client,
            content,
            chapter_num,
            snapshot_holder,
            model,
            chapter_version=version,
            global_user_prompt=global_user_prompt,
            background_story=meta.background_story,
            protagonist_bio=meta.protagonist_bio,
            writing_demand=meta.writing_demand,
            xp_mode=xp_mode or meta.xp_mode,
        )
        self.save_world_bible(title, snapshot_holder)
        report = self.rebuild_world_bible_from_active(
            client,
            title,
            model=model,
            global_user_prompt=global_user_prompt,
            xp_mode=xp_mode,
        )
        report["refreshed_chapter"] = chapter_num
        report["refreshed_version"] = version
        return report

    # ========== 🔬 智能前情提要选取算法 ==========

    def load_smart_summary(
        self,
        title: str,
        client=None,
        next_chapter_num: int | None = None,
        max_recent: int = 3,
        model: str = "deepseek-v4-flash",
        global_user_prompt: str = "",
    ) -> str:
        """
        智能选取前情提要：短篇全量返回，长篇自动压缩早期章节。

        算法策略:
        - 总章节 ≤ max_recent + 2（默认 5 章） → 返回完整 plot_summary
        - 总章节 > max_recent + 2:
            1. 保留最近 max_recent 章的完整摘要（详细剧情参考）
            2. 早期章节 → 使用缓存的压缩摘要（若无可调用 API 生成）
            3. 拼接返回

        Args:
            title: 小说名
            client: OpenAI 客户端（压缩时首次需要，之后用缓存）
            next_chapter_num: 当前要生成的第几章（用于判断哪些是早期/近期）
            max_recent: 保留完整详情的最近章节数

        Returns:
            选取/压缩后的前情提要文本
        """
        entries = [entry for entry in self._active_summary_entries(title) if entry.get("summary")]
        full_summary = self.build_active_path_summary(title)

        # 情况 1：没有章节或很少 → 直接返回完整内容
        total = len(entries)
        if total <= max_recent + 2:
            return full_summary

        # 情况 2：长篇小说 → 按活跃路径截取
        recent_entries = entries[-max_recent:]
        early_entries = entries[:-max_recent]
        early_texts = []
        recent_texts = []

        for entry in early_entries:
            early_texts.append(f"第{entry['chapter_num']}章：{entry['summary']}")
        for entry in recent_entries:
            recent_texts.append(
                f"第{entry['chapter_num']}章「{entry['title']}」摘要：{entry['summary']}"
            )

        # 早期章节压缩
        compressed_early = ""
        meta = self.load_meta(title)
        if meta.compressed_early_summary:
            # 使用缓存
            compressed_early = meta.compressed_early_summary
        elif client is not None and early_texts:
            # 无缓存，调用 API 压缩
            try:
                early_block = "\n\n".join(early_texts)
                from utils.prompts import Prompts
                xp_hint = ""
                if meta.xp_mode:
                    xp_hint = f"\n4. 仍会影响后续的激情内容\n\n{Prompts.XP_SUMMARY_GUIDE}"
                response = client.chat.completions.create(
                    model=model,
                    messages=[{
                        "role": "user",
                        "content": (
                            f"以下是某小说早期章节的剧情摘要集合。"
                            f"请用 300 字以内提炼出其中**对后续剧情仍然重要**的内容：\n"
                            f"1. 尚未解决的核心冲突/悬念\n"
                            f"2. 仍然活跃的人物及其当前关系\n"
                            f"3. 仍在发挥作用的世界设定\n"
                            f"{xp_hint}\n"
                            f"忽略已完结的支线和不再重要的细节。简洁为主：\n\n{early_block}"
                            + (f"\n\n用户偏好参考: {global_user_prompt}" if global_user_prompt.strip() else "")
                        ),
                    }],
                    max_tokens=600,
                    temperature=0.3,
                )
                compressed_early = response.choices[0].message.content or ""
                # 缓存成功压缩的结果
                meta.compressed_early_summary = compressed_early
                self._save_meta(title, meta)
            except Exception:
                # 压缩失败时，仅返回最近 max_recent 章的摘要 + 早期章节计数提示
                compressed_early = (
                    f"（前 {len(early_texts)} 章因篇幅过长已压缩。"
                    f"如需完整前情，请查阅 plot_summary.txt）"
                )

        # 拼接最终前情提要
        parts = []
        if compressed_early:
            parts.append(f"📌 【早期剧情概要（前 {len(early_texts)} 章压缩）】\n{compressed_early}\n")
        if recent_texts:
            parts.append(f"📋 【近期发展（最近 {len(recent_texts)} 章）】\n")
            parts.extend(recent_texts)
            parts.append("")

        result = "\n".join(parts)
        return result.strip() or full_summary

    def _extract_chapter_summary(self, title: str, chapter_num: int) -> str:
        """
        从章节树活跃路径提取指定章节摘要；找不到时兼容旧 plot_summary 文本。
        """
        try:
            meta = self.ensure_chapter_tree(title)
            for node_id in meta.active_path:
                node = meta.chapter_nodes.get(node_id)
                if node and int(node.get("chapter_num", 0) or 0) == chapter_num:
                    summary = (node.get("summary") or "").strip()
                    if summary:
                        return summary
                    break
        except Exception:
            pass
        legacy_text = self._read_encrypted_text(self._summary_path(title)) or ""
        legacy = self._legacy_extract_chapter_summary(legacy_text, chapter_num)
        if legacy:
            return legacy
        return "[摘要不可用]"

    def build_continuity_contract(
        self,
        title: str,
        chapter_num: int,
        chapter_title: str = "",
        plot_content: str = "",
        *,
        max_characters: int = 6,
        max_threads: int = 6,
        max_foreshadowing: int = 8,
    ) -> str:
        """构造下一章生成前的连贯性契约，供 prompt 和逻辑审稿复用。"""
        parts = [f"【本章连贯性契约】\n目标章节：第{chapter_num}章「{chapter_title or f'第{chapter_num}章'}」"]
        previous_summary = self._extract_chapter_summary(title, chapter_num - 1)
        if previous_summary and previous_summary != "[摘要不可用]":
            parts.append(f"上章结尾/承接点：\n{previous_summary[:1200]}")
        try:
            bible = self.load_world_bible(title)
        except Exception:
            bible = None

        if bible:
            active_chars = [
                c for c in bible.characters
                if c.current_location or c.current_goal or c.current_emotion or c.recent_action or c.knowledge_state
            ]
            if active_chars:
                lines = []
                for ch in active_chars[:max_characters]:
                    fields = []
                    if ch.current_location:
                        fields.append(f"位置={ch.current_location}")
                    if ch.current_goal:
                        fields.append(f"目标={ch.current_goal}")
                    if ch.current_emotion:
                        fields.append(f"状态={ch.current_emotion}")
                    if ch.knowledge_state:
                        fields.append(f"已知={ch.knowledge_state}")
                    if ch.recent_action:
                        fields.append(f"近况={ch.recent_action}")
                    if ch.unresolved_conflicts:
                        fields.append("未解冲突=" + "；".join(ch.unresolved_conflicts[:2]))
                    lines.append(f"- {ch.name}：" + "；".join(fields))
                parts.append("角色状态必须承接：\n" + "\n".join(lines))

            active_threads = [p for p in bible.active_plot_threads if p.status == "active"]
            if active_threads:
                lines = []
                for p in active_threads[:max_threads]:
                    line = f"- {p.name}：{p.description[:120]}"
                    if p.expected_payoff:
                        line += f"；预期回收={p.expected_payoff[:80]}"
                    if p.payoff_hint:
                        line += f"；推进提示={p.payoff_hint[:80]}"
                    if p.last_touched_chapter:
                        line += f"；最近触达=第{p.last_touched_chapter}章"
                    lines.append(line)
                parts.append("活跃剧情线必须延续：\n" + "\n".join(lines))

            open_foreshadowing = [
                f for f in bible.global_foreshadowing
                if f.get("status", "open") not in ("resolved", "已回收")
            ]
            if open_foreshadowing:
                lines = []
                for f in open_foreshadowing[:max_foreshadowing]:
                    line = f"- {f.get('hint', '')}"
                    if f.get("status"):
                        line += f" [{f.get('status')}]"
                    if f.get("relates_to"):
                        line += f"；关联={f.get('relates_to')}"
                    if f.get("next_step"):
                        line += f"；下次推进={f.get('next_step')}"
                    if f.get("reveal_rule"):
                        line += f"；回收限制={f.get('reveal_rule')}"
                    lines.append(line)
                parts.append("伏笔状态机：\n" + "\n".join(lines))

        if plot_content.strip():
            parts.append(f"本章已定情节必须兑现：\n{plot_content[:1200]}")
        parts.append("生成时禁止：无交代跳时间/换地点、角色动机突变、角色知道未获得的信息、设定规则前后冲突、伏笔无铺垫突然揭底。")
        return "\n\n".join(parts)

    def build_author_planning_prompt(self, title: str) -> str:
        """返回独立作者规划层。规划是未来意图，不视为已发生事实。"""
        meta = self.load_meta(title)
        plan = NovelMeta._coerce_text(meta.author_plan).strip()
        if not plan:
            return ""
        return (
            "【作者规划层（未来写作意图，不等同于已发生事实）】\n"
            "用途：控制主线目标、阶段目标、人物弧光、本卷主题、节奏要求和禁写事项。\n"
            "规则：可以按规划铺垫和推进，但不得把尚未写出的规划当作角色已知信息或正文既成事实。\n"
            f"{plan}"
        )

    def clear_compressed_cache(self, title: str) -> None:
        """清除早期章节压缩缓存（章节切换或重建摘要后调用）"""
        meta = self.load_meta(title)
        meta.compressed_early_summary = ""
        self._save_meta(title, meta)

    # ========== 生成摘要（调用 API） ==========

    def generate_summary(
        self,
        client,
        chapter_content: str,
        chapter_num: int,
        chapter_title: str,
        model: str = "deepseek-v4-flash",
        global_user_prompt: str = "",
        xp_mode: bool = False,
        raise_on_error: bool = False,
    ) -> str:
        """
        调用 API 生成章节摘要

        Args:
            client: OpenAI 客户端实例
            chapter_content: 章节正文
            chapter_num: 章节编号
            chapter_title: 章节标题

        Returns:
            生成的摘要文本
        """
        from utils.prompts import Prompts

        summary_guide = Prompts.CONTINUITY_SUMMARY_GUIDE
        xp_hint = f"\n\n{Prompts.XP_SUMMARY_GUIDE}" if xp_mode else ""
        request_kwargs = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": (
                    f"请为以下小说片段（第{chapter_num}章「{chapter_title}」）"
                    f"生成一段用于后续续写的结构化剧情记忆。\n\n{summary_guide}\n\n章节正文：\n{chapter_content}"
                    + xp_hint
                    + (f"\n\n用户偏好参考: {global_user_prompt}" if global_user_prompt.strip() else "")
                ),
            }],
            "max_tokens": 2000,
            "temperature": 0.3,
        }
        last_error: Exception | None = None
        retryable_names = {
            "APIConnectionError",
            "APITimeoutError",
            "RateLimitError",
            "InternalServerError",
        }
        for attempt in range(3):
            try:
                response = client.chat.completions.create(**request_kwargs)
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                if type(exc).__name__ not in retryable_names or attempt == 2:
                    break
                time.sleep(1.5 * (attempt + 1))

        if raise_on_error and last_error is not None:
            cause = getattr(last_error, "__cause__", None)
            detail = str(cause).strip() if cause else ""
            if detail and detail not in str(last_error):
                raise RuntimeError(f"{last_error}\n底层原因：{detail}") from last_error
            raise last_error
        return ""

    # ========== 生成历史记录 ==========

    def _history_dir(self, title: str) -> str:
        """生成历史记录目录"""
        return os.path.join(self._book_dir(title), ".generation_history")

    def save_generation_record(
        self,
        title: str,
        chapter_num: int,
        chapter_title: str,
        version: int,
        prompt: str,
        model: str,
        temperature: float,
        top_p: float,
        max_tokens: int,
        frequency_penalty: float,
        content_preview: str,
        requirement: str = "",
        plot: str = "",
    ) -> str:
        """
        保存一次生成的完整配置记录（独立文件）

        Args:
            title: 小说标题
            chapter_num: 章节编号
            chapter_title: 章节标题
            version: 保存的版本号
            prompt: 完整 User Prompt
            model: 使用的模型
            temperature: 温度
            top_p: top_p
            max_tokens: 最大 token 数
            frequency_penalty: 频率惩罚
            content_preview: 生成内容前 500 字（用于摘要参考）
            requirement: 续写要求（续写模式专用，用于重新生成时还原）
            plot: 续写剧情走向 / AI 建议方向 / 已定情节（用于重新生成时还原）

        Returns:
            保存的文件路径
        """
        history_dir = self._history_dir(title)
        os.makedirs(history_dir, exist_ok=True)

        record = {
            "chapter_num": chapter_num,
            "chapter_title": chapter_title,
            "version": version,
            "prompt": prompt,
            "model": model,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "frequency_penalty": frequency_penalty,
            "content_preview": content_preview,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if requirement:
            record["requirement"] = requirement
        if plot:
            record["plot"] = plot

        filename = f"ch{chapter_num:04d}_v{version:03d}.json"
        file_path = os.path.join(history_dir, filename)
        self._write_encrypted_json(file_path, record)
        written_path = self._encrypt_path(file_path)
        return written_path

    def load_generation_record(self, title: str, chapter_num: int, version: int) -> dict | None:
        """加载指定章节和版本的生成记录"""
        history_dir = self._history_dir(title)
        filename = f"ch{chapter_num:04d}_v{version:03d}.json"
        fpath = os.path.join(history_dir, filename)
        try:
            return self._read_encrypted_json(fpath)
        except Exception:
            return None

    def load_generation_history(self, title: str) -> list[dict]:
        """加载指定小说的所有历史记录，按章节号排序"""
        history_dir = self._history_dir(title)
        if not os.path.isdir(history_dir):
            return []

        records = []
        for fname in sorted(os.listdir(history_dir)):
            match_name = fname[:-4] if fname.endswith(".enc") else fname
            if not match_name.endswith(".json"):
                continue
            fpath = os.path.join(history_dir, match_name)
            try:
                data = self._read_encrypted_json(fpath)
                if data is not None:
                    records.append(data)
            except Exception:
                continue
        return records

    def build_history_summary(self, title: str, exclude_chapter: int | None = None) -> str:
        """
        从历史记录总结各章节的生成配置与内容概要

        用于写下一章时，回顾前面活跃章节的生成配置与内容风格。

        Args:
            title: 小说标题
            exclude_chapter: 要排除的章节（如正在生成的章节），None 则不排除

        Returns:
            格式化的历史摘要文本，供下一章生成时参考
        """
        records = self.load_generation_history(title)
        if not records:
            return "暂无历史记录。"

        # 按章节号分组，取每个章节的最新版本记录
        chapter_records: dict[int, dict] = {}
        for rec in records:
            cn = rec["chapter_num"]
            if exclude_chapter is not None and cn == exclude_chapter:
                continue
            existing = chapter_records.get(cn)
            if existing is None or rec["version"] > existing["version"]:
                chapter_records[cn] = rec

        if not chapter_records:
            return "暂无历史记录（排除当前章节后）。"

        lines = [f"📋 已从历史记录中总结 {len(chapter_records)} 章的生成配置：\n"]
        for cn in sorted(chapter_records):
            rec = chapter_records[cn]
            lines.append(
                f"--- 第{cn}章「{rec.get('chapter_title', '')}」---\n"
                f"  模型: {rec.get('model', '未知')} | 温度: {rec.get('temperature', '?')}\n"
                f"  内容概要: {rec.get('content_preview', '')[:200]}...\n"
            )

        return "\n".join(lines)

    # ========== 内部辅助 ==========

    def _meta_path(self, title: str) -> str:
        return os.path.join(self._book_dir(title), "meta.json")

    def _summary_path(self, title: str) -> str:
        return os.path.join(self._book_dir(title), "plot_summary.txt")

    def _world_bible_path(self, title: str) -> str:
        return os.path.join(self._book_dir(title), "world_bible.json")

    def load_world_bible(self, title: str):
        """加载小说的世界书，返回 WorldBible 对象，不存在则返回空 WorldBible"""
        from core.world_bible import WorldBible, dict_to_world_bible
        wb_path = self._world_bible_path(title)
        try:
            data = self._read_encrypted_json(wb_path)
            if data is not None:
                return dict_to_world_bible(data)
        except Exception:
            return WorldBible()
        return WorldBible()

    def save_world_bible(self, title: str, bible) -> None:
        """保存世界书到文件"""
        from core.world_bible import world_bible_to_dict
        wb_path = self._world_bible_path(title)
        os.makedirs(os.path.dirname(wb_path), exist_ok=True)
        data = world_bible_to_dict(bible)
        self._write_encrypted_json(wb_path, data)



