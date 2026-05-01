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
from dataclasses import dataclass, field, asdict
from datetime import datetime

# 书架根目录（相对于项目根目录）
BOOKSHELF_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bookshelf")


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
    created_at: str = ""
    updated_at: str = ""
    total_chapters: int = 0
    chapter_titles: list[str] = field(default_factory=list)
    # 章节版本管理：key = 章节编号(str), value = ChapterInfo
    chapter_versions: dict[str, dict] = field(default_factory=dict)
    # 早期章节压缩缓存：{ "compressed_early": "..." }
    compressed_early_summary: str = ""


class NovelManager:
    """小说管理器：书架+章节+摘要+版本管理"""

    def __init__(self, bookshelf_root: str | None = None):
        self._bookshelf_root = bookshelf_root or BOOKSHELF_DIR
        os.makedirs(self._bookshelf_root, exist_ok=True)

    # ========== 书架操作 ==========

    def list_books(self) -> list[str]:
        """列出书架上所有小说（目录名）"""
        if not os.path.isdir(self._bookshelf_root):
            return []
        return sorted(
            d for d in os.listdir(self._bookshelf_root)
            if os.path.isdir(os.path.join(self._bookshelf_root, d))
        )

    def create_book(self, title: str) -> str:
        """创建新小说目录，返回该书的目录路径"""
        book_dir = self._book_dir(title)
        os.makedirs(book_dir, exist_ok=True)

        meta = NovelMeta(
            title=title,
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        self._save_meta(title, meta)
        # 创建空摘要文件
        summary_path = self._summary_path(title)
        if not os.path.exists(summary_path):
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write("故事刚刚开始。\n")
        return book_dir

    def delete_book(self, title: str) -> bool:
        """删除小说及其所有章节，不可恢复"""
        book_dir = self._book_dir(title)
        if os.path.isdir(book_dir):
            shutil.rmtree(book_dir)
            return True
        return False

    def load_meta(self, title: str) -> NovelMeta:
        """加载小说元信息，若不存在则返回默认空 meta"""
        meta_path = self._meta_path(title)
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return NovelMeta(**data)
        return NovelMeta(title=title)

    def save_meta(self, title: str, **kwargs) -> NovelMeta:
        """更新小说元信息（部分字段）并保存"""
        meta = self.load_meta(title)
        for key, value in kwargs.items():
            if hasattr(meta, key):
                setattr(meta, key, value)
        meta.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._save_meta(title, meta)
        return meta

    def _save_meta(self, title: str, meta: NovelMeta) -> None:
        meta_path = self._meta_path(title)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(asdict(meta), f, ensure_ascii=False, indent=2)

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

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

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

        self._save_meta(title, meta)
        return file_path, version

    def set_active_version(self, title: str, chapter_num: int, version: int) -> None:
        """设置某章节的活跃版本（用于计入剧情摘要）"""
        meta = self.load_meta(title)
        key = str(chapter_num)
        if key in meta.chapter_versions:
            meta.chapter_versions[key]["active"] = version
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
            if fname.startswith(prefix) and fname.endswith(suffix):
                with open(os.path.join(book_dir, fname), "r", encoding="utf-8") as f:
                    return f.read()
        return None

    def read_active_chapter(self, title: str, chapter_num: int) -> str | None:
        """读取某章节的活跃版本内容"""
        active_v = self.get_active_version(title, chapter_num)
        if active_v is None:
            return None
        return self.read_chapter_version(title, chapter_num, active_v)

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
        deleted = False
        for fname in os.listdir(book_dir):
            if fname.startswith(prefix) and fname.endswith(suffix):
                os.remove(os.path.join(book_dir, fname))
                deleted = True
                break

        if not deleted:
            return False

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

        return True

    def delete_chapter(self, title: str, chapter_num: int) -> bool:
        """删除某章节的所有版本"""
        book_dir = self._book_dir(title)
        if not os.path.isdir(book_dir):
            return False

        prefix = f"第{chapter_num}章"
        deleted = False
        for fname in list(os.listdir(book_dir)):
            if fname.startswith(prefix) and fname.endswith(".txt"):
                os.remove(os.path.join(book_dir, fname))
                deleted = True

        if deleted:
            meta = self.load_meta(title)
            key = str(chapter_num)
            meta.chapter_versions.pop(key, None)
            meta.total_chapters = max(
                (int(k) for k in meta.chapter_versions.keys()),
                default=0,
            )
            self._save_meta(title, meta)

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
        """加载小说的全部前情提要"""
        summary_path = self._summary_path(title)
        if os.path.exists(summary_path):
            with open(summary_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        return "故事刚刚开始。"

    def append_summary(
        self,
        title: str,
        chapter_num: int,
        chapter_title: str,
        summary_text: str,
    ) -> None:
        """追加一章的摘要到摘要文件（仅在生成新活跃版本时调用）"""
        summary_path = self._summary_path(title)
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(f"\n第{chapter_num}章「{chapter_title}」摘要：{summary_text}\n")

    def rebuild_summary_from_active(self, client, title: str) -> None:
        """
        根据所有活跃章节重新生成完整 plot_summary.txt
        当用户切换活跃版本后调用此方法重建摘要
        """
        chapters = self.list_chapters(title)
        if not chapters:
            # 没有章节，重置摘要
            summary_path = self._summary_path(title)
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write("故事刚刚开始。\n")
            return

        full_summary_parts = ["# 完整前情提要（基于活跃章节自动生成）\n"]
        for ch in chapters:
            content = self.read_active_chapter(title, ch["num"])
            if not content:
                continue
            try:
                response = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[{
                        "role": "user",
                        "content": (
                            f"请为以下小说片段（第{ch['num']}章「{ch['title']}」）"
                            f"写一段 800 字以内的剧情梗概，"
                            f"重点记录人物动向、关键事件和新出现的设定：\n\n{content}"
                        ),
                    }],
                    max_tokens=2000,
                    temperature=0.3,
                )
                summary = response.choices[0].message.content or ""
            except Exception as e:
                summary = f"[摘要生成失败: {e}]"

            full_summary_parts.append(
                f"\n第{ch['num']}章「{ch['title']}」摘要：{summary}\n"
            )

        summary_path = self._summary_path(title)
        with open(summary_path, "w", encoding="utf-8") as f:
            f.writelines(full_summary_parts)

        # 重建后清空压缩缓存，下次 load_smart_summary 会重新计算
        meta = self.load_meta(title)
        meta.compressed_early_summary = ""
        self._save_meta(title, meta)

    # ========== 🔬 智能前情提要选取算法 ==========

    def load_smart_summary(
        self,
        title: str,
        client=None,
        next_chapter_num: int | None = None,
        max_recent: int = 3,
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
        full_summary = self.load_summary(title)
        chapters = self.list_chapters(title)

        # 情况 1：没有章节或很少 → 直接返回完整内容
        total = len(chapters)
        if total <= max_recent + 2:
            return full_summary

        # 情况 2：长篇小说 → 智能截取
        # 按章节编号排序
        sorted_ch = sorted(chapters, key=lambda c: c["num"])
        all_nums = [c["num"] for c in sorted_ch]

        # 最近的 max_recent 章
        recent_nums = set(all_nums[-max_recent:])
        recent_parts = []
        early_parts = []

        # 解析摘要文件，按章节号归类
        early_texts = []
        recent_texts = []

        for ch in sorted_ch:
            # 从摘要文本中提取对应章节的内容
            ch_summary = self._extract_chapter_summary(full_summary, ch["num"])
            if ch["num"] in recent_nums:
                recent_texts.append(f"第{ch['num']}章「{ch['title']}」摘要：{ch_summary}")
            else:
                early_texts.append(f"第{ch['num']}章：{ch_summary}")

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
                response = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[{
                        "role": "user",
                        "content": (
                            f"以下是某小说早期章节的剧情摘要集合。"
                            f"请用 200-300 字提炼出其中对后续剧情**仍然重要**的核心事件、"
                            f"人物关系变化和世界设定。忽略已完结的支线和不再重要的细节：\n\n{early_block}"
                        ),
                    }],
                    max_tokens=600,
                    temperature=0.3,
                )
                compressed_early = response.choices[0].message.content or ""
            except Exception:
                # 压缩失败时，仅返回最近 max_recent 章的摘要 + 早期章节计数提示
                compressed_early = (
                    f"（前 {len(early_texts)} 章因篇幅过长已压缩。"
                    f"如需完整前情，请查阅 plot_summary.txt）"
                )

            # 缓存压缩结果
            if compressed_early:
                meta.compressed_early_summary = compressed_early
                self._save_meta(title, meta)

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

    def _extract_chapter_summary(self, full_summary: str, chapter_num: int) -> str:
        """
        从完整的 plot_summary 文本中提取指定章节的摘要内容

        匹配模式：第N章「xxx」摘要：...（直到下一个第M章或文件末尾）
        """
        pattern = rf"第{chapter_num}章「.*?」摘要：(.*?)(?=\n第\d+章「|$)"
        match = re.search(pattern, full_summary, re.DOTALL)
        if match:
            return match.group(1).strip()
        # 回退：找不到时返回空
        return "[摘要不可用]"

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
        try:
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{
                    "role": "user",
                    "content": (
                        f"请为以下小说片段（第{chapter_num}章「{chapter_title}」）"
                        f"写一段 800 字以内的剧情梗概，"
                        f"重点记录人物动向、关键事件和新出现的设定：\n\n{chapter_content}"
                    ),
                }],
                max_tokens=2000,
                temperature=0.3,  # 摘要用低温保证准确性
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            return f"[摘要生成失败: {e}]"

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

        filename = f"ch{chapter_num:04d}_v{version:03d}.json"
        file_path = os.path.join(history_dir, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return file_path

    def load_generation_history(self, title: str) -> list[dict]:
        """加载指定小说的所有历史记录，按章节号排序"""
        history_dir = self._history_dir(title)
        if not os.path.isdir(history_dir):
            return []

        records = []
        for fname in sorted(os.listdir(history_dir)):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(history_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    records.append(json.load(f))
            except (json.JSONDecodeError, KeyError):
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

    def _book_dir(self, title: str) -> str:
        safe = title.replace("/", "-").replace("\\", "-").replace(":", "：")
        return os.path.join(self._bookshelf_root, safe)

    def _meta_path(self, title: str) -> str:
        return os.path.join(self._book_dir(title), "meta.json")

    def _summary_path(self, title: str) -> str:
        return os.path.join(self._book_dir(title), "plot_summary.txt")