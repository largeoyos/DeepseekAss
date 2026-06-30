from __future__ import annotations

import asyncio
import queue
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.novel_manager import NovelMeta
from web.services import (
    WebApiConfigError,
    WebAuthError,
    WebRuntime,
    WebUserContext,
    task_event_to_sse,
)


class LoginRequest(BaseModel):
    username: str
    password: str


class BookCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


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


def create_app(runtime: WebRuntime | None = None) -> FastAPI:
    runtime = runtime or WebRuntime()
    app = FastAPI(title="DeepseekAss Web MVP", version="0.1.0")
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

    @app.post("/api/auth/login")
    def login(payload: LoginRequest):
        try:
            return runtime.login(payload.username, payload.password)
        except WebAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.post("/api/auth/logout")
    def logout(token: str = Depends(token_from_header)):
        runtime.logout(token)
        return {"ok": True}

    @app.get("/api/session")
    def session(ctx: WebUserContext = Depends(current_context)):
        api_configured = bool((ctx.load_api_config().get("text") or {}).get("api_key"))
        return {"user": {"username": ctx.username}, "api_configured": api_configured}

    @app.get("/api/books")
    def list_books(ctx: WebUserContext = Depends(current_context)):
        return {"books": [{"title": title} for title in ctx.novel_manager.list_books()]}

    @app.post("/api/books")
    def create_book(payload: BookCreateRequest, ctx: WebUserContext = Depends(current_context)):
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="书名不能为空")
        path = ctx.novel_manager.create_book(title)
        return {"title": title, "path": path}

    @app.get("/api/books/{title}/meta")
    def get_meta(title: str, ctx: WebUserContext = Depends(current_context)):
        return {"meta": serialize_meta(ctx.novel_manager.load_meta(title))}

    @app.put("/api/books/{title}/meta")
    def save_meta(title: str, payload: MetaUpdateRequest, ctx: WebUserContext = Depends(current_context)):
        meta = ctx.novel_manager.save_meta(title, **model_data(payload))
        return {"meta": serialize_meta(meta)}

    @app.get("/api/books/{title}/chapters")
    def list_chapters(title: str, ctx: WebUserContext = Depends(current_context)):
        return {"chapters": ctx.novel_manager.list_chapters(title)}

    @app.get("/api/books/{title}/chapters/{chapter_num}")
    def read_chapter(title: str, chapter_num: int, ctx: WebUserContext = Depends(current_context)):
        content = ctx.novel_manager.read_active_chapter(title, chapter_num)
        if content is None:
            raise HTTPException(status_code=404, detail="章节不存在")
        chapters = ctx.novel_manager.list_chapters(title)
        current = next((item for item in chapters if int(item.get("chapter_num", 0)) == chapter_num), {})
        return {"chapter": current, "content": content}

    @app.post("/api/books/{title}/generate")
    def generate(title: str, payload: GenerateRequest, ctx: WebUserContext = Depends(current_context)):
        try:
            task_id = runtime.start_generation(
                ctx,
                title=title,
                chapter_title=payload.chapter_title.strip(),
                plot=payload.plot,
                target_words=payload.target_words,
            )
            return {"task_id": task_id}
        except WebApiConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str, ctx: WebUserContext = Depends(current_context)):
        if not runtime.user_owns_task(ctx.username, task_id):
            raise HTTPException(status_code=404, detail="任务不存在")
        task = runtime.serialize_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        return {"task": task}

    @app.get("/api/tasks/{task_id}/events")
    async def task_events(
        request: Request,
        task_id: str,
        token: str = Query(default=""),
    ):
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

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


def serialize_meta(meta: NovelMeta) -> dict:
    return {
        "title": meta.title,
        "author": meta.author,
        "protagonist_bio": meta.protagonist_bio,
        "background_story": meta.background_story,
        "writing_demand": meta.writing_demand,
        "author_plan": meta.author_plan,
        "genre": meta.genre,
        "style_tone": meta.style_tone,
        "xp_mode": bool(meta.xp_mode),
        "total_chapters": meta.total_chapters,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
    }


def model_data(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


app = create_app()

