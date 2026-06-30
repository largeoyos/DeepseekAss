from __future__ import annotations

import json
import os
import queue
import secrets
import threading
import time
from dataclasses import asdict
from typing import Callable

from config import Config
from core.app_services import ChapterGenerationService
from core.auth_manager import AuthManager
from core.chat_client import DeepSeekChatClient
from core.novel_manager import NovelManager, NovelMeta
from core.settings_manager import SettingsManager
from core.task_manager import TaskEvent, TaskRunner
from strategies.novel_strategy import NovelStrategy


class WebAuthError(Exception):
    pass


class WebApiConfigError(Exception):
    pass


class TokenStore:
    def __init__(self, ttl_seconds: int = 12 * 60 * 60) -> None:
        self.ttl_seconds = ttl_seconds
        self._tokens: dict[str, dict] = {}
        self._lock = threading.Lock()

    def issue(self, username: str, enc_key: bytes) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[token] = {
                "username": username,
                "enc_key": enc_key,
                "expires_at": time.time() + self.ttl_seconds,
            }
        return token

    def revoke(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    def resolve(self, token: str) -> tuple[str, bytes]:
        with self._lock:
            payload = self._tokens.get(token)
            if not payload:
                raise WebAuthError("登录已失效，请重新登录")
            if float(payload["expires_at"]) < time.time():
                self._tokens.pop(token, None)
                raise WebAuthError("登录已过期，请重新登录")
            return str(payload["username"]), payload["enc_key"]


class WebUserContext:
    def __init__(self, username: str, enc_key: bytes) -> None:
        self.username = username
        self.enc_key = enc_key
        self.user_dir = AuthManager.get_user_dir(username)
        os.makedirs(self.user_dir, exist_ok=True)
        self.settings_manager = SettingsManager(self.user_dir, AuthManager, enc_key)
        self.settings = self.settings_manager.load()
        self.bookshelf_root = os.path.join(self.user_dir, "bookshelf")
        self.novel_manager = NovelManager(
            self.bookshelf_root,
            crypto=AuthManager,
            enc_key=enc_key,
        )
        self.novel_manager.configure_retrieval(self.settings.get("retrieval") or {})

    def reload_settings(self) -> None:
        self.settings = self.settings_manager.load()

    def load_api_config(self) -> dict:
        self.reload_settings()
        config_path = os.path.join(self.user_dir, "config.enc")
        data = AuthManager.decrypt_json(self.enc_key, config_path) or {}
        if data and "text" not in data:
            data = {
                "text": {
                    "api_key": data.get("api_key", ""),
                    "base_url": data.get("base_url", Config.BASE_URL),
                    "model": data.get("model") or self.settings.get("last_model") or Config.MODEL_V4_FLASH,
                },
                "image": {
                    "api_key": data.get("image_api_key", ""),
                    "base_url": data.get("image_base_url", ""),
                    "model": data.get("image_model", Config.IMAGE_MODEL),
                },
            }
        text = dict(data.get("text") or {})
        image = dict(data.get("image") or {})
        text.setdefault("api_key", Config.API_KEY)
        text.setdefault("base_url", Config.BASE_URL)
        text.setdefault("model", self.settings.get("last_model") or Config.MODEL_V4_FLASH)
        image.setdefault("api_key", Config.IMAGE_API_KEY)
        image.setdefault("base_url", Config.IMAGE_BASE_URL)
        image.setdefault("model", Config.IMAGE_MODEL)
        return {"text": text, "image": image}

    def require_text_api(self) -> dict:
        api_config = self.load_api_config()
        text = api_config.get("text") or {}
        if not str(text.get("api_key") or "").strip():
            raise WebApiConfigError("请先在桌面端设置中心配置文字 API")
        return api_config


class WebRuntime:
    def __init__(
        self,
        *,
        token_ttl_seconds: int = 12 * 60 * 60,
        client_factory: Callable[[dict], object] | None = None,
    ) -> None:
        self.tokens = TokenStore(ttl_seconds=token_ttl_seconds)
        self.client_factory = client_factory or self._default_client_factory
        self._task_queues: dict[str, queue.Queue[TaskEvent]] = {}
        self._task_events: dict[str, list[TaskEvent]] = {}
        self._task_users: dict[str, str] = {}
        self._task_runner = TaskRunner(event_sink=self._on_task_event)
        self._task_lock = threading.Lock()
        self._generation_locks: dict[str, threading.Lock] = {}

    def login(self, username: str, password: str) -> dict:
        ok, enc_key = AuthManager.authenticate(username, password)
        if not ok or enc_key is None:
            raise WebAuthError("用户名或密码错误")
        token = self.tokens.issue(username, enc_key)
        return {"token": token, "user": {"username": username}}

    def logout(self, token: str) -> None:
        self.tokens.revoke(token)

    def context_from_token(self, token: str) -> WebUserContext:
        username, enc_key = self.tokens.resolve(token)
        return WebUserContext(username, enc_key)

    def serialize_task(self, task_id: str) -> dict | None:
        record = self._task_runner.get_record(task_id)
        return asdict(record) if record else None

    def user_owns_task(self, username: str, task_id: str) -> bool:
        return self._task_users.get(task_id) == username

    def start_generation(
        self,
        ctx: WebUserContext,
        *,
        title: str,
        chapter_title: str,
        plot: str,
        target_words: int,
    ) -> str:
        api_config = ctx.require_text_api()
        lock = self._generation_locks.setdefault(ctx.username, threading.Lock())
        if not lock.acquire(blocking=False):
            raise RuntimeError("当前账号已有生成任务在运行，请稍后再试")

        def target(handle):
            try:
                return self._run_generation_task(
                    handle,
                    ctx=ctx,
                    api_config=api_config,
                    title=title,
                    chapter_title=chapter_title,
                    plot=plot,
                    target_words=target_words,
                )
            finally:
                lock.release()

        handle = self._task_runner.start(
            f"生成《{title}》下一章",
            target,
            retryable=False,
            metadata={"kind": "chapter_generation", "book": title, "username": ctx.username},
        )
        self._task_users[handle.task_id] = ctx.username
        return handle.task_id

    def event_queue(self, task_id: str) -> queue.Queue[TaskEvent]:
        with self._task_lock:
            return self._task_queues.setdefault(task_id, queue.Queue())

    def events_snapshot(self, task_id: str) -> list[TaskEvent]:
        with self._task_lock:
            return list(self._task_events.get(task_id, []))

    def _on_task_event(self, event: TaskEvent) -> None:
        with self._task_lock:
            self._task_events.setdefault(event.task_id, []).append(event)
            if len(self._task_events[event.task_id]) > 250:
                self._task_events[event.task_id] = self._task_events[event.task_id][-250:]
            q = self._task_queues.setdefault(event.task_id, queue.Queue())
            q.put(event)

    def _default_client_factory(self, api_config: dict):
        text = api_config.get("text") or {}
        return DeepSeekChatClient._create_openai_client(
            str(text.get("api_key") or ""),
            str(text.get("base_url") or Config.BASE_URL),
        )

    def _run_generation_task(
        self,
        handle,
        *,
        ctx: WebUserContext,
        api_config: dict,
        title: str,
        chapter_title: str,
        plot: str,
        target_words: int,
    ) -> dict:
        manager = ctx.novel_manager
        service = ChapterGenerationService(manager)
        params = generation_params(ctx.settings, api_config)
        raw_client = self.client_factory(api_config)

        handle.progress("准备章节上下文", percent=5, stage="准备上下文")
        meta = manager.load_meta(title)
        chapter_num = manager.get_next_chapter_num(title)
        if not chapter_title.strip():
            chapter_title = f"第{chapter_num}章"
        context_report = service.build_context(
            title,
            chapter_num,
            chapter_title,
            plot,
            global_prompt=str(ctx.settings.get("global_user_prompt") or ""),
            client=raw_client,
            model=params["model"],
        )
        prompt = build_generation_prompt(
            title=title,
            chapter_num=chapter_num,
            chapter_title=chapter_title,
            plot=plot,
            target_words=target_words,
            meta=meta,
            context_text=context_report.render(),
        )

        strategy = NovelStrategy()
        strategy.novel_title = title
        strategy.chapter_title = chapter_title
        strategy.protagonist_bio = meta.protagonist_bio
        strategy.background_story = meta.background_story
        strategy.writing_demand = meta.writing_demand
        strategy.genre = meta.genre
        strategy.style_tone = meta.style_tone
        strategy.xp_mode = bool(meta.xp_mode)
        strategy.chapter_mode = True
        messages = [
            {"role": "system", "content": strategy.get_system_prompt()},
            *strategy.build_system_messages(),
            {"role": "user", "content": prompt},
        ]

        handle.progress("正在生成正文", percent=20, stage="生成正文")
        content = self._stream_completion(handle, raw_client, messages, params)
        if not content.strip():
            raise RuntimeError("模型未返回章节正文")

        handle.progress("保存章节版本", percent=62, stage="保存章节")
        _, saved_version = service.persist_chapter(
            title=title,
            chapter_num=chapter_num,
            chapter_title=chapter_title,
            content=content,
            version=manager.get_next_version(title, chapter_num),
            parent_id=None,
            prompt=prompt,
            model=params["model"],
            temperature=params["temperature"],
            top_p=params["top_p"],
            max_tokens=params["max_tokens"],
            frequency_penalty=params["frequency_penalty"],
            requirement=meta.writing_demand,
            plot=plot,
            generation_mode="classic-web",
        )

        warnings: list[str] = []
        handle.progress("生成章节摘要", percent=74, stage="生成摘要")
        try:
            manager.generate_summary(
                raw_client,
                content,
                chapter_num,
                chapter_title,
                model=params["model"],
                global_user_prompt=str(ctx.settings.get("global_user_prompt") or ""),
                xp_mode=bool(meta.xp_mode),
                raise_on_error=True,
            )
        except Exception as exc:
            warnings.append(f"摘要生成失败：{exc}")
            handle.progress("摘要生成失败，章节已保存", percent=78, stage="生成摘要")

        handle.progress("更新世界书", percent=84, stage="更新世界书")
        try:
            service.world_bible.sync_chapter(
                raw_client,
                title,
                chapter_num,
                saved_version,
                content,
                model=params["model"],
                global_user_prompt=str(ctx.settings.get("global_user_prompt") or ""),
                xp_mode=bool(meta.xp_mode),
            )
        except Exception as exc:
            warnings.append(f"世界书更新失败：{exc}")
            handle.progress("世界书更新失败，章节已保存", percent=88, stage="更新世界书")

        handle.progress("创建项目快照", percent=93, stage="创建快照")
        try:
            service.create_auto_snapshot(title, chapter_num, saved_version)
        except Exception as exc:
            warnings.append(f"快照创建失败：{exc}")
            handle.progress("快照创建失败，章节已保存", percent=96, stage="创建快照")

        result = {
            "title": title,
            "chapter_num": chapter_num,
            "chapter_title": chapter_title,
            "version": saved_version,
            "warnings": warnings,
            "preview": content[:240],
        }
        handle.progress("生成完成", percent=100, stage="完成", data={"result": result})
        return result

    def _stream_completion(self, handle, raw_client, messages: list[dict], params: dict) -> str:
        chunks: list[str] = []
        response = raw_client.chat.completions.create(
            model=params["model"],
            messages=messages,
            temperature=params["temperature"],
            top_p=params["top_p"],
            max_tokens=params["max_tokens"],
            frequency_penalty=params["frequency_penalty"],
            stream=True,
        )
        seen = 0
        for chunk in response:
            if handle.cancelled:
                raise RuntimeError("任务已取消")
            delta = chunk.choices[0].delta
            text = getattr(delta, "content", None) or ""
            if not text:
                continue
            chunks.append(text)
            seen += len(text)
            if seen >= 80:
                handle.progress(
                    "生成正文中",
                    percent=min(60, 20 + len("".join(chunks)) // 160),
                    stage="生成正文",
                    data={"text": "".join(chunks[-8:])[-500:]},
                )
                seen = 0
        if seen:
            handle.progress(
                "生成正文中",
                percent=min(60, 20 + len("".join(chunks)) // 160),
                stage="生成正文",
                data={"text": "".join(chunks[-8:])[-500:]},
            )
        return "".join(chunks).strip()


def generation_params(settings: dict, api_config: dict) -> dict:
    text = api_config.get("text") or {}
    presets = settings.get("presets") or {}
    preset_name = settings.get("current_preset") or ""
    preset = presets.get(preset_name) or {}
    max_tokens = int(preset.get("max_tokens") or NovelStrategy().recommended_max_tokens)
    return {
        "model": text.get("model") or settings.get("last_model") or Config.MODEL_V4_FLASH,
        "temperature": _scale_preset_value(preset.get("temperature"), 0.85),
        "top_p": _scale_preset_value(preset.get("top_p"), 0.9),
        "frequency_penalty": _scale_preset_value(preset.get("frequency_penalty"), 0.5),
        "max_tokens": max(512, min(32768, max_tokens)),
    }


def _scale_preset_value(value, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number / 100 if number > 2 else number


def build_generation_prompt(
    *,
    title: str,
    chapter_num: int,
    chapter_title: str,
    plot: str,
    target_words: int,
    meta: NovelMeta,
    context_text: str,
) -> str:
    plan = meta.author_plan.strip() if isinstance(meta.author_plan, str) else ""
    demand = meta.writing_demand.strip() if isinstance(meta.writing_demand, str) else ""
    parts = [
        f"请为小说《{title}》生成第{chapter_num}章《{chapter_title}》。",
        f"目标字数：约 {target_words} 字，优先保证完整场景和可续写性。",
    ]
    if plot.strip():
        parts.append(f"本章必须兑现的情节：\n{plot.strip()}")
    if demand:
        parts.append(f"写作要求：\n{demand}")
    if plan:
        parts.append(f"作者规划参考：\n{plan}")
    parts.append(f"续写上下文：\n{context_text}")
    parts.append("只输出章节正文，不要输出解释、提纲、Markdown 标题或附加说明。")
    return "\n\n".join(parts)


def task_event_to_sse(event: TaskEvent) -> str:
    payload = {
        "task_id": event.task_id,
        "type": event.type,
        "message": event.message,
        "data": event.data,
    }
    return f"event: {event.type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
