
from __future__ import annotations

import asyncio
import os
import queue
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.auth_manager import AuthManager
from core.character_book import CharacterProfile, character_book_to_dict
from core.novel_manager import NovelMeta
from core.world_bible import audit_world_bible_consistency, dict_to_world_bible, world_bible_to_dict
from core.world_bible_diff import diff_world_bibles, summarize_world_bible_diff
from utils.export import export_book, export_chapter
from utils.summarize import detect_sections, split_text_locally
from web.services import WebApiConfigError, WebAuthError, WebRuntime, WebSensitiveError, WebUserContext, masked_api_config, task_event_to_sse

class LoginRequest(BaseModel):
    username: str
    password: str

class SensitiveConfirmRequest(BaseModel):
    password: str

class ApiConfigRequest(BaseModel):
    text: dict = Field(default_factory=dict)
    image: dict = Field(default_factory=dict)

class SettingsUpdateRequest(BaseModel):
    settings: dict = Field(default_factory=dict)

class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6)

class BookCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)

class BookRenameRequest(BaseModel):
    new_title: str = Field(min_length=1, max_length=120)

class MetaUpdateRequest(BaseModel):
    protagonist_bio: str = ""
    background_story: str = ""
    writing_demand: str = ""
    author_plan: str = ""
    genre: str = ""
    style_tone: str = ""

class GenerateRequest(BaseModel):
    chapter_title: str = ""
    plot: str = ""
    target_words: int = Field(default=3000, ge=500, le=30000)

class ExportRequest(BaseModel):
    fmt: str = "txt"
    chapter_num: int | None = None

class WorldSaveRequest(BaseModel):
    world: dict = Field(default_factory=dict)

class WorldEntityRequest(BaseModel):
    category: str
    data: dict = Field(default_factory=dict)
    index: int | None = None

class AgentAdvisorRequest(BaseModel):
    message: str
    manual_references: list[str] = Field(default_factory=list)
    fiction_context: bool = True

class ContinuationSegmentRequest(BaseModel):
    text: str

class ContinuationImportRequest(BaseModel):
    title: str
    sections: list[dict]

class MarkdownWriteRequest(BaseModel):
    path: str
    content: str = ""

class RoleProfileRequest(BaseModel):
    profile: dict = Field(default_factory=dict)

class ConversationSaveRequest(BaseModel):
    record: dict = Field(default_factory=dict)

class RoleChatRequest(BaseModel):
    title: str = "角色对话"
    message: str
    character_ids: list[str] = Field(default_factory=list)
    conversation_id: str = ""

def create_app(runtime: WebRuntime | None = None) -> FastAPI:
    runtime = runtime or WebRuntime()
    app = FastAPI(title="DeepseekAss Web", version="0.3.0")
    app.state.runtime = runtime

    def token_from_header(authorization: str = Header(default="")) -> str:
        if not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="未登录")
        return authorization.split(" ", 1)[1].strip()

    def current_context(token: str = Depends(token_from_header)) -> WebUserContext:
        try:
            return runtime.context_from_token(token)
        except WebAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    def require_sensitive(ctx: WebUserContext, ticket: str) -> None:
        try:
            runtime.require_sensitive(ctx.username, ticket)
        except WebSensitiveError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/api/auth/login", tags=["auth"])
    def login(payload: LoginRequest):
        try:
            return runtime.login(payload.username, payload.password)
        except WebAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.post("/api/auth/logout", tags=["auth"])
    def logout(token: str = Depends(token_from_header)):
        runtime.logout(token)
        return {"ok": True}

    @app.post("/api/auth/confirm", tags=["auth"])
    def confirm_sensitive(payload: SensitiveConfirmRequest, ctx: WebUserContext = Depends(current_context)):
        try:
            ticket = runtime.confirm_sensitive(ctx.username, payload.password)
            return {"sensitive_ticket": ticket, "expires_in": 600}
        except WebSensitiveError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.get("/api/session", tags=["auth"])
    def session(ctx: WebUserContext = Depends(current_context)):
        api_config = ctx.load_api_config()
        return {"user": {"username": ctx.username}, "api_configured": bool((api_config.get("text") or {}).get("api_key")), "settings": ctx.settings, "api": masked_api_config(api_config)}

    @app.get("/api/settings", tags=["settings"])
    def get_settings(ctx: WebUserContext = Depends(current_context)):
        return {"settings": ctx.settings_manager.load(), "api": masked_api_config(ctx.load_api_config())}

    @app.put("/api/settings", tags=["settings"])
    def save_settings(payload: SettingsUpdateRequest, ctx: WebUserContext = Depends(current_context)):
        settings = ctx.settings_manager.load()
        settings.update(payload.settings or {})
        ctx.settings_manager.save(settings)
        return {"settings": settings}

    @app.put("/api/settings/api", tags=["settings"])
    def save_api_config(payload: ApiConfigRequest, sensitive_ticket: str = Header(default="", alias="X-Sensitive-Ticket"), ctx: WebUserContext = Depends(current_context)):
        require_sensitive(ctx, sensitive_ticket)
        current = ctx.load_api_config()
        for section in ("text", "image"):
            update = dict(getattr(payload, section) or {})
            if update.get("api_key") in {"", "***"}:
                update.pop("api_key", None)
            current[section].update(update)
        ctx.save_api_config(current)
        return {"api": masked_api_config(current)}

    @app.post("/api/settings/password", tags=["settings"])
    def change_password(payload: PasswordChangeRequest, ctx: WebUserContext = Depends(current_context)):
        try:
            AuthManager.change_password(ctx.username, payload.old_password, payload.new_password)
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/settings/test-connection", tags=["settings"])
    def test_connection(ctx: WebUserContext = Depends(current_context)):
        try:
            api_config = ctx.require_text_api()
            client = runtime.client_factory(api_config)
            model = (api_config.get("text") or {}).get("model") or "deepseek-v4-flash"
            response = client.chat.completions.create(model=model, messages=[{"role": "user", "content": "只回复 OK"}], max_tokens=16, temperature=0)
            return {"ok": True, "reply": response.choices[0].message.content}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/books", tags=["books"])
    def list_books(ctx: WebUserContext = Depends(current_context)):
        return {"books": [{"title": title} for title in ctx.novel_manager.list_books()]}

    @app.post("/api/books", tags=["books"])
    def create_book(payload: BookCreateRequest, ctx: WebUserContext = Depends(current_context)):
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="书名不能为空")
        path = ctx.novel_manager.create_book(title)
        return {"title": title, "path": path}

    @app.patch("/api/books/{title}", tags=["books"])
    def rename_book(title: str, payload: BookRenameRequest, ctx: WebUserContext = Depends(current_context)):
        ok = ctx.novel_manager.rename_book(title, payload.new_title.strip())
        if not ok:
            raise HTTPException(status_code=404, detail="书籍不存在或重命名失败")
        return {"title": payload.new_title.strip()}

    @app.delete("/api/books/{title}", tags=["books"])
    def delete_book(title: str, ctx: WebUserContext = Depends(current_context)):
        ok = ctx.novel_manager.delete_book(title)
        if not ok:
            raise HTTPException(status_code=404, detail="书籍不存在")
        return {"ok": True}

    @app.get("/api/books/{title}/meta", tags=["books"])
    def get_meta(title: str, ctx: WebUserContext = Depends(current_context)):
        return {"meta": serialize_meta(ctx.novel_manager.load_meta(title))}

    @app.put("/api/books/{title}/meta", tags=["books"])
    def save_meta(title: str, payload: MetaUpdateRequest, ctx: WebUserContext = Depends(current_context)):
        meta = ctx.novel_manager.save_meta(title, **model_data(payload))
        return {"meta": serialize_meta(meta)}

    @app.post("/api/books/{title}/generate", tags=["generation"])
    def generate(title: str, payload: GenerateRequest, ctx: WebUserContext = Depends(current_context)):
        try:
            task_id = runtime.start_generation(ctx, title=title, chapter_title=payload.chapter_title.strip(), plot=payload.plot, target_words=payload.target_words)
            return {"task_id": task_id}
        except WebApiConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/books/{title}/chapters", tags=["chapters"])
    def list_chapters(title: str, ctx: WebUserContext = Depends(current_context)):
        return {"chapters": [normalize_chapter(item) for item in ctx.novel_manager.list_chapters(title)]}

    @app.get("/api/books/{title}/chapters/{chapter_num}", tags=["chapters"])
    def read_chapter(title: str, chapter_num: int, ctx: WebUserContext = Depends(current_context)):
        content = ctx.novel_manager.read_active_chapter(title, chapter_num)
        if content is None:
            raise HTTPException(status_code=404, detail="章节不存在")
        chapters = [normalize_chapter(item) for item in ctx.novel_manager.list_chapters(title)]
        current = next((item for item in chapters if int(item.get("chapter_num", 0)) == chapter_num), {})
        return {"chapter": current, "content": content}

    @app.get("/api/books/{title}/chapter-tree", tags=["chapters"])
    def chapter_tree(title: str, ctx: WebUserContext = Depends(current_context)):
        meta = ctx.novel_manager.ensure_chapter_tree(title)
        return {"trees": ctx.novel_manager.list_chapter_trees(title), "nodes": ctx.novel_manager.list_chapter_tree_nodes(title), "active_path": meta.active_path, "active_tree_id": meta.active_tree_id, "target": ctx.novel_manager.get_active_generation_target(title)}

    @app.get("/api/books/{title}/nodes/{node_id}", tags=["chapters"])
    def read_node(title: str, node_id: str, ctx: WebUserContext = Depends(current_context)):
        content = ctx.novel_manager.read_chapter_node(title, node_id)
        if content is None:
            raise HTTPException(status_code=404, detail="节点不存在")
        meta = ctx.novel_manager.ensure_chapter_tree(title)
        return {"node": meta.chapter_nodes.get(node_id), "content": content}

    @app.post("/api/books/{title}/nodes/{node_id}/activate", tags=["chapters"])
    def activate_node(title: str, node_id: str, ctx: WebUserContext = Depends(current_context)):
        if not ctx.novel_manager.switch_active_node(title, node_id):
            raise HTTPException(status_code=404, detail="节点不存在")
        try:
            ctx.novel_manager.rebuild_plot_summary_from_tree(title)
        except Exception:
            pass
        return {"ok": True}

    @app.delete("/api/books/{title}/nodes/{node_id}", tags=["chapters"])
    def delete_node(title: str, node_id: str, ctx: WebUserContext = Depends(current_context)):
        if not ctx.novel_manager.delete_chapter_node(title, node_id):
            raise HTTPException(status_code=404, detail="节点不存在或无法删除")
        return {"ok": True}

    @app.delete("/api/books/{title}/chapters/{chapter_num}", tags=["chapters"])
    def delete_chapter(title: str, chapter_num: int, ctx: WebUserContext = Depends(current_context)):
        if not ctx.novel_manager.delete_chapter(title, chapter_num):
            raise HTTPException(status_code=404, detail="章节不存在")
        return {"ok": True}

    @app.get("/api/books/{title}/context-preview", tags=["chapters"])
    def context_preview(title: str, chapter_title: str = "", plot: str = "", ctx: WebUserContext = Depends(current_context)):
        target = ctx.novel_manager.get_active_generation_target(title)
        report = ctx.novel_manager.context_assembler().assemble_chapter(title, int(target.get("chapter_num") or 1), chapter_title or f"第{target.get('chapter_num') or 1}章", plot, global_prompt=str(ctx.settings.get("global_user_prompt") or ""))
        return {"preview": report.preview(), "content": report.render(), "sections": [asdict(item) for item in report.sections]}

    @app.post("/api/books/{title}/export", tags=["export"])
    def export_book_task(title: str, payload: ExportRequest, ctx: WebUserContext = Depends(current_context)):
        fmt = normalize_fmt(payload.fmt)
        def target(handle):
            runtime.cleanup_exports(ctx)
            handle.progress("准备导出", percent=10, stage="导出")
            if payload.chapter_num:
                out = os.path.join(ctx.export_root, f"{safe_name(title)}_第{payload.chapter_num}章.{fmt}")
                path = export_chapter(ctx.novel_manager, title, int(payload.chapter_num), fmt, out)
            else:
                out = os.path.join(ctx.export_root, f"{safe_name(title)}_全书.{fmt}")
                path = export_book(ctx.novel_manager, title, fmt, out)
            download = runtime.register_download(ctx.username, path, os.path.basename(path), media_type_for(path))
            handle.progress("导出完成", percent=100, stage="完成", data={"download": download})
            return download
        return {"task_id": runtime.start_task(ctx.username, f"导出《{title}》", target, metadata={"kind": "export", "book": title})}

    @app.get("/api/books/{title}/world", tags=["world"])
    def get_world(title: str, ctx: WebUserContext = Depends(current_context)):
        bible = ctx.novel_manager.load_world_bible(title)
        return {"world": world_bible_to_dict(bible), "warnings": getattr(bible, "consistency_warnings", [])}

    @app.put("/api/books/{title}/world", tags=["world"])
    def save_world(title: str, payload: WorldSaveRequest, ctx: WebUserContext = Depends(current_context)):
        before = ctx.novel_manager.load_world_bible(title)
        after = dict_to_world_bible(payload.world)
        diff = diff_world_bibles(before, after)
        ctx.novel_manager.save_world_bible(title, after, force=True)
        return {"ok": True, "diff": [asdict(item) for item in diff], "summary": summarize_world_bible_diff(diff)}

    @app.post("/api/books/{title}/world/entity", tags=["world"])
    def upsert_world_entity(title: str, payload: WorldEntityRequest, ctx: WebUserContext = Depends(current_context)):
        data = world_bible_to_dict(ctx.novel_manager.load_world_bible(title))
        bucket = data.setdefault(payload.category, [])
        if not isinstance(bucket, list):
            raise HTTPException(status_code=400, detail="该分类不是列表")
        if payload.index is None:
            bucket.append(dict(payload.data or {}))
        else:
            if payload.index < 0 or payload.index >= len(bucket):
                raise HTTPException(status_code=404, detail="条目不存在")
            bucket[payload.index] = dict(payload.data or {})
        bible = dict_to_world_bible(data)
        ctx.novel_manager.save_world_bible(title, bible, force=True)
        return {"world": world_bible_to_dict(bible)}

    @app.get("/api/books/{title}/world/audit", tags=["world"])
    def world_audit(title: str, ctx: WebUserContext = Depends(current_context)):
        return {"warnings": audit_world_bible_consistency(ctx.novel_manager.load_world_bible(title))}

    @app.post("/api/books/{title}/snapshots", tags=["snapshots"])
    def create_snapshot(title: str, message: str = "", ctx: WebUserContext = Depends(current_context)):
        snap = ctx.novel_manager.snapshot_service(title).create(message or "Web 手动快照", source="web")
        return {"snapshot": asdict(snap)}

    @app.get("/api/books/{title}/snapshots", tags=["snapshots"])
    def list_snapshots(title: str, ctx: WebUserContext = Depends(current_context)):
        return {"snapshots": [asdict(item) for item in ctx.novel_manager.snapshot_service(title).list()]}

    @app.post("/api/books/{title}/agent/advisor", tags=["agent"])
    def ask_advisor(title: str, payload: AgentAdvisorRequest, ctx: WebUserContext = Depends(current_context)):
        api_config = ctx.require_text_api()
        def target(handle):
            from core.agent.advisor import AdvisorRequest, WritingAdvisorService
            client = runtime.client_factory(api_config)
            model = (api_config.get("text") or {}).get("model") or "deepseek-v4-flash"
            handle.progress("Agent 顾问思考中", percent=20, stage="Agent")
            result = WritingAdvisorService(ctx.novel_manager, client, ctx.conversation_manager).ask(AdvisorRequest(title, payload.message, model, ctx.settings, payload.manual_references, payload.fiction_context))
            handle.progress("Agent 顾问完成", percent=100, stage="完成", data={"result": asdict(result)})
            return asdict(result)
        return {"task_id": runtime.start_task(ctx.username, f"Agent 顾问《{title}》", target, metadata={"kind": "agent_advisor", "book": title})}

    @app.post("/api/books/{title}/agent/chapter/plan", tags=["agent"])
    def agent_chapter_plan(title: str, payload: GenerateRequest, ctx: WebUserContext = Depends(current_context)):
        api_config = ctx.require_text_api()
        def target(handle):
            from core.agent.chapter_generation import AgentChapterGenerationService, AgentChapterRequest
            client = runtime.client_factory(api_config)
            model = (api_config.get("text") or {}).get("model") or "deepseek-v4-flash"
            target_info = ctx.novel_manager.get_active_generation_target(title)
            chapter_num = int(target_info.get("chapter_num") or 1)
            chapter_title = payload.chapter_title or f"第{chapter_num}章"
            handle.progress("Agent 规划章节", percent=15, stage="Agent 规划")
            plan = AgentChapterGenerationService(ctx.novel_manager, client).prepare(AgentChapterRequest(title, chapter_num, chapter_title, payload.plot, "", payload.target_words, model, [], str(ctx.settings.get("global_user_prompt") or "")))
            handle.progress("Agent 规划完成", percent=100, stage="完成", data={"plan": plan.to_dict(), "rendered": plan.render()})
            return {"plan": plan.to_dict(), "rendered": plan.render()}
        return {"task_id": runtime.start_task(ctx.username, f"Agent 规划《{title}》", target, metadata={"kind": "agent_chapter_plan", "book": title})}

    @app.post("/api/continuation/segment", tags=["continuation"])
    def continuation_segment(payload: ContinuationSegmentRequest, ctx: WebUserContext = Depends(current_context)):
        sections = detect_sections(payload.text) or split_text_locally(payload.text, max_chars=6000)
        return {"sections": [{"title": title, "content": content} for title, content in sections]}

    @app.post("/api/continuation/import", tags=["continuation"])
    def continuation_import(payload: ContinuationImportRequest, ctx: WebUserContext = Depends(current_context)):
        def target(handle):
            title = payload.title.strip() or "续写作品"
            if title not in ctx.novel_manager.list_books():
                ctx.novel_manager.create_book(title)
            for idx, section in enumerate(payload.sections, start=1):
                if handle.cancelled:
                    raise RuntimeError("任务已取消")
                chapter_title = str(section.get("title") or f"导入段落 {idx}")
                content = str(section.get("content") or "")
                if content.strip():
                    ctx.novel_manager.save_chapter_version(title, ctx.novel_manager.get_next_chapter_num(title), chapter_title, content)
                handle.progress(f"已导入 {idx}/{len(payload.sections)}", percent=min(95, 10 + idx * 80 // max(1, len(payload.sections))), stage="续写导入")
            handle.progress("导入完成", percent=100, stage="完成", data={"title": title})
            return {"title": title, "count": len(payload.sections)}
        return {"task_id": runtime.start_task(ctx.username, f"导入续写《{payload.title}》", target, metadata={"kind": "continuation_import"})}

    @app.get("/api/markdown/tree", tags=["markdown"])
    def markdown_tree(ctx: WebUserContext = Depends(current_context)):
        items = []
        root = os.path.abspath(ctx.markdown_root)
        for current, dirs, files in os.walk(root):
            rel_dir = os.path.relpath(current, root).replace("\\", "/")
            rel_dir = "" if rel_dir == "." else rel_dir
            for name in sorted(dirs):
                items.append({"path": f"{rel_dir}/{name}".strip("/"), "name": name, "type": "folder"})
            for name in sorted(files):
                items.append({"path": f"{rel_dir}/{name}".strip("/"), "name": name, "type": "file"})
        return {"items": items}

    @app.get("/api/markdown/file", tags=["markdown"])
    def read_markdown(path: str, ctx: WebUserContext = Depends(current_context)):
        try:
            return {"path": path, "content": ctx.read_markdown(path)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="文件不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/markdown/file", tags=["markdown"])
    def write_markdown(payload: MarkdownWriteRequest, ctx: WebUserContext = Depends(current_context)):
        try:
            ctx.write_markdown(payload.path, payload.content)
            return {"ok": True, "path": payload.path}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/roleplay/characters", tags=["roleplay"])
    def list_characters(ctx: WebUserContext = Depends(current_context)):
        return {"book": character_book_to_dict(ctx.character_book_manager.load())}

    @app.post("/api/roleplay/characters", tags=["roleplay"])
    def create_character(payload: RoleProfileRequest, ctx: WebUserContext = Depends(current_context)):
        profile = CharacterProfile(**filter_profile(payload.profile))
        created = ctx.character_book_manager.create_profile(profile)
        return {"profile": asdict(created)}

    @app.get("/api/roleplay/conversations", tags=["roleplay"])
    def list_conversations(ctx: WebUserContext = Depends(current_context)):
        return {"conversations": [asdict(item) for item in ctx.conversation_manager.list_conversations()]}

    @app.post("/api/roleplay/conversations", tags=["roleplay"])
    def save_conversation(payload: ConversationSaveRequest, ctx: WebUserContext = Depends(current_context)):
        record = payload.record or {}
        conversation_id = record.get("conversation_id") or ctx.conversation_manager.generate_id(record.get("title") or "角色对话")
        ctx.conversation_manager.save_conversation(conversation_id=conversation_id, title=record.get("title") or "角色对话", model=record.get("model") or "", messages=record.get("messages") or [], strategy=record.get("strategy") or "角色扮演", chat_type=record.get("chat_type") or "private", participant_character_ids=record.get("participant_character_ids") or [])
        return {"conversation_id": conversation_id}

    @app.get("/api/tasks", tags=["tasks"])
    def list_tasks(ctx: WebUserContext = Depends(current_context)):
        return {"tasks": runtime.list_tasks(ctx.username)}

    @app.get("/api/tasks/{task_id}", tags=["tasks"])
    def get_task(task_id: str, ctx: WebUserContext = Depends(current_context)):
        if not runtime.user_owns_task(ctx.username, task_id):
            raise HTTPException(status_code=404, detail="任务不存在")
        task = runtime.serialize_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        return {"task": task}

    @app.post("/api/tasks/{task_id}/cancel", tags=["tasks"])
    def cancel_task(task_id: str, ctx: WebUserContext = Depends(current_context)):
        return {"ok": runtime.cancel_task(ctx.username, task_id)}

    @app.get("/api/tasks/{task_id}/events", tags=["tasks"])
    async def task_events(request: Request, task_id: str, token: str = Query(default="")):
        try:
            ctx = runtime.context_from_token(token)
        except WebAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        if not runtime.user_owns_task(ctx.username, task_id):
            raise HTTPException(status_code=404, detail="任务不存在")
        async def event_stream():
            replayed_terminal = False
            for event in runtime.events_snapshot(task_id):
                yield task_event_to_sse(event)
                if event.type in {"completed", "failed", "cancelled"}:
                    replayed_terminal = True
            if replayed_terminal:
                return
            q = runtime.event_queue(task_id)
            while not await request.is_disconnected():
                try:
                    event = await asyncio.to_thread(q.get, True, 15)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield task_event_to_sse(event)
                if event.type in {"completed", "failed", "cancelled"}:
                    break
        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/api/downloads/{download_id}", tags=["downloads"])
    def download(download_id: str, token: str = Query(default="")):
        try:
            ctx = runtime.context_from_token(token)
            item = runtime.resolve_download(ctx.username, download_id)
        except (WebAuthError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="下载不存在或已过期") from exc
        return FileResponse(item["path"], media_type=item.get("media_type"), filename=item.get("filename"))

    @app.get("/api/token-log", tags=["diagnostics"])
    def token_log(ctx: WebUserContext = Depends(current_context)):
        entries = [asdict(item) for item in ctx.token_log_manager.list_entries()]
        return {"entries": entries, "total": len(entries)}

    @app.get("/api/diagnostics", tags=["diagnostics"])
    def diagnostics(ctx: WebUserContext = Depends(current_context)):
        return {"user": ctx.username, "books": len(ctx.novel_manager.list_books()), "conversations": len(ctx.conversation_manager.list_conversations()), "api_configured": bool((ctx.load_api_config().get("text") or {}).get("api_key")), "settings_keys": sorted(ctx.settings.keys()), "time": time.strftime("%Y-%m-%d %H:%M:%S")}

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


def serialize_meta(meta: NovelMeta) -> dict:
    return {"title": meta.title, "author": meta.author, "protagonist_bio": meta.protagonist_bio, "background_story": meta.background_story, "writing_demand": meta.writing_demand, "author_plan": meta.author_plan, "genre": meta.genre, "style_tone": meta.style_tone, "xp_mode": bool(meta.xp_mode), "total_chapters": meta.total_chapters, "created_at": meta.created_at, "updated_at": meta.updated_at}


def normalize_chapter(item: dict) -> dict:
    data = dict(item)
    num = data.get("chapter_num", data.get("num", 0))
    data["chapter_num"] = int(num or 0)
    data.setdefault("num", data["chapter_num"])
    data.setdefault("title", data.get("chapter_title") or f"第{data['chapter_num']}章")
    return data


def model_data(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def filter_profile(data: dict) -> dict:
    return {key: value for key, value in (data or {}).items() if key in CharacterProfile.__dataclass_fields__}


def safe_name(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in text)[:80] or "export"


def normalize_fmt(fmt: str) -> str:
    fmt = (fmt or "txt").lower().strip(".")
    if fmt not in {"txt", "md", "html", "docx"}:
        raise HTTPException(status_code=400, detail="不支持的导出格式")
    return fmt


def media_type_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8", ".html": "text/html; charset=utf-8", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}.get(ext, "application/octet-stream")


app = create_app()
