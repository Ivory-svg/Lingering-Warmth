"""余温的 FastAPI 入口。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextlib import suppress
from importlib.resources import files
import os
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from yuwen.chat import ChatModel, ChatService
from yuwen.auth import AuthService, COOKIE_NAME
from yuwen.catalog import CatalogService
from yuwen.config import AppConfig, load_config, validate_auth_password
from yuwen.context import ContextBuilder
from yuwen.embedder import EmbeddingModel, MemoryEmbedder
from yuwen.export import build_export, build_markdown_export
from yuwen.gatekeeper import GatekeeperService
from yuwen.handoff import HandoffService
from yuwen.inflow import InflowService
from yuwen.llm import EmbeddingClient, LLMClient
from yuwen.store import Store
from yuwen.surfacing import SurfacingService
from yuwen.worldbook import WorldbookReader


class ChatRequest(BaseModel):
    content: str


class ChatOOC(BaseModel):
    original: str
    reason: str
    voice_note_id: int


class ChatResponse(BaseModel):
    message_id: int
    reply: str
    session_id: int
    ooc_rewritten: bool
    ooc: ChatOOC | None
    handoff_happened: bool


class MessageOOC(ChatOOC):
    rewritten: str
    source: str


class MessageResponse(BaseModel):
    message_id: int
    role: str
    content: str
    created_at: str
    ooc: MessageOOC | None = None


class OOCFlagRequest(BaseModel):
    message_id: int
    player_rewrite: str | None = None


class OOCFlagResponse(BaseModel):
    verdict: str
    reason: str
    rewritten: str | None
    voice_note_id: int | None


class VoiceNotePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool


class OverturnRequest(BaseModel):
    note: str | None = None


class OverturnResponse(BaseModel):
    message_id: int
    content: str
    restored: bool


class LoginRequest(BaseModel):
    password: str


class MemoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str | None = None
    quote: str | None = None
    emotion_text: str | None = None
    reason: str | None = None


class MemorySeal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sealed: bool


class MemoryStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["open", "done", "dropped"] | None
    note: str = Field(default="", max_length=100)


def create_app(
    config: AppConfig,
    *,
    store: Store | None = None,
    llm: ChatModel | None = None,
    worldbook: WorldbookReader | None = None,
    embedding_client: EmbeddingModel | None = None,
) -> FastAPI:
    database = store or Store(config.storage.db_path)
    model = llm or LLMClient(config.llm)
    books = worldbook or WorldbookReader(config.character)
    vector_client = (
        embedding_client or EmbeddingClient(config.embedding)
        if config.embedding.base_url else None
    )
    embedder = (
        MemoryEmbedder(database, vector_client, config.embedding)
        if vector_client is not None else None
    )
    context = ContextBuilder(
        database,
        books,
        voice_notes_limit=config.gatekeeper.voice_notes_in_context,
        scan_depth=config.worldbook.scan_depth,
        entry_budget=config.worldbook.entry_budget,
        corpus_sample=config.worldbook.corpus_sample,
        todo=config.todo,
    )
    inflow_service = InflowService(
        database,
        model,
        character_name=config.character.name,
        player_name=config.character.player_name,
        every_n_turns=config.inflow.every_n_turns,
        embedder=embedder,
        todo=config.todo,
    )
    handoff_service = HandoffService(
        database,
        model,
        context_limit=config.llm.context_limit,
        trigger_ratio=config.handoff.trigger_ratio,
        raw_tail=config.handoff.raw_tail,
        min_turns=config.handoff.min_turns,
        before_handoff=inflow_service.flush_pending,
        player_name=config.character.player_name,
    )
    gatekeeper_service = GatekeeperService(
        database,
        books,
        model,
        scan_depth=config.worldbook.scan_depth,
        entry_budget=config.worldbook.entry_budget,
        character_name=config.character.name,
        player_name=config.character.player_name,
    )
    surfacing_service = SurfacingService(
        database,
        config=config.surfacing,
        decay=config.decay,
        embedding=config.embedding,
        todo=config.todo,
        client=vector_client,
    )
    chat_service = ChatService(
        database,
        context,
        model,
        handoff=handoff_service,
        gatekeeper=gatekeeper_service if config.gatekeeper.pre_send else None,
        surfacing=surfacing_service,
    )
    auth_service = AuthService(database, config.auth)
    catalog_service = CatalogService(database, surfacing_service)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        validate_auth_password(config.auth)
        database.initialize()
        books.core()
        books.warn_oversized_entries(config.worldbook.entry_budget)
        backfill_task = asyncio.create_task(embedder.backfill()) if embedder else None
        try:
            yield
        finally:
            if backfill_task is not None:
                backfill_task.cancel()
                with suppress(asyncio.CancelledError):
                    await backfill_task
            if isinstance(vector_client, EmbeddingClient):
                await vector_client.aclose()
            if isinstance(model, LLMClient):
                await model.aclose()

    app = FastAPI(title="余温", version="0.1.0", lifespan=lifespan)
    app.state.config = config
    app.state.store = database
    app.state.chat_service = chat_service
    app.state.inflow_service = inflow_service
    app.state.embedder_service = embedder
    app.state.handoff_service = handoff_service
    app.state.gatekeeper_service = gatekeeper_service
    app.state.surfacing_service = surfacing_service
    app.state.auth_service = auth_service
    app.state.catalog_service = catalog_service

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        allowed = {("GET", "/login"), ("POST", "/login"), ("GET", "/health")}
        if (request.method, request.url.path) not in allowed:
            if not auth_service.is_authenticated(request.cookies.get(COOKIE_NAME)):
                if request.method == "GET" and request.url.path in {"/", "/catalog"}:
                    return RedirectResponse("/login", status_code=303)
                return JSONResponse({"detail": "请先登录"}, status_code=401)
        if request.method in {"POST", "PATCH"}:
            media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                return JSONResponse({"detail": "请使用 application/json"}, status_code=415)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    async def get_index() -> str:
        return files("yuwen.static").joinpath("index.html").read_text(encoding="utf-8")

    @app.get("/catalog", response_class=HTMLResponse)
    async def get_catalog_page() -> str:
        return files("yuwen.static").joinpath("catalog.html").read_text(encoding="utf-8")

    @app.get("/login", response_class=HTMLResponse)
    async def get_login(request: Request) -> Response:
        if auth_service.is_authenticated(request.cookies.get(COOKIE_NAME)):
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(
            files("yuwen.static").joinpath("login.html").read_text(encoding="utf-8")
        )

    @app.post("/login")
    async def post_login(request: Request, body: LoginRequest) -> Response:
        ip = request.client.host if request.client else "unknown"
        retry = auth_service.retry_after(ip)
        if retry:
            return JSONResponse(
                {"detail": "试错太多次了，过一会儿再来", "retry_after_seconds": retry},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        if not auth_service.check_password(body.password):
            retry = auth_service.record_failure(ip)
            if retry:
                return JSONResponse(
                    {"detail": "试错太多次了，过一会儿再来", "retry_after_seconds": retry},
                    status_code=429,
                    headers={"Retry-After": str(retry)},
                )
            raise HTTPException(status_code=401, detail="密码不对")
        auth_service.clear_failures(ip)
        token = auth_service.create_session()
        response = JSONResponse({"ok": True})
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=config.auth.session_days * 86_400,
            secure=config.auth.secure_cookie,
        )
        return response

    @app.post("/logout")
    async def post_logout(request: Request) -> Response:
        auth_service.logout(request.cookies.get(COOKIE_NAME))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.get("/memories")
    async def get_memories(
        filter: Literal["all", "unclear", "fading", "sealed", "open"] = "all",
        q: str = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
    ) -> dict[str, object]:
        return catalog_service.list(filter=filter, q=q, offset=offset, limit=limit)

    @app.get("/memories/{memory_id}")
    async def get_memory(memory_id: int) -> dict[str, object]:
        try:
            return catalog_service.detail(memory_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="找不到这条记忆") from exc

    @app.patch("/memories/{memory_id}")
    async def patch_memory(
        memory_id: int, request: MemoryPatch, background_tasks: BackgroundTasks
    ) -> dict[str, object]:
        try:
            result, changed = catalog_service.correct(
                memory_id, summary=request.summary, quote=request.quote,
                emotion_text=request.emotion_text, reason=request.reason,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="找不到这条记忆") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if changed and embedder is not None:
            background_tasks.add_task(embedder.embed_memory, memory_id)
        return result

    @app.post("/memories/{memory_id}/seal")
    async def post_memory_seal(memory_id: int, request: MemorySeal) -> dict[str, object]:
        try:
            return catalog_service.seal(memory_id, request.sealed)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="找不到这条记忆") from exc

    @app.post("/memories/{memory_id}/status")
    async def post_memory_status(memory_id: int, request: MemoryStatus) -> dict[str, object]:
        try:
            return catalog_service.set_status(memory_id, request.status, request.note)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="找不到这条记忆") from exc

    @app.post("/chat", response_model=ChatResponse)
    async def post_chat(
        request: ChatRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, object]:
        try:
            result = await chat_service.handle_message(request.content)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if embedder is not None and result.handoff_memory_id is not None:
            background_tasks.add_task(embedder.embed_memory, result.handoff_memory_id)
        background_tasks.add_task(
            inflow_service.process_turn,
            result.user_message_id,
            result.message_id,
        )
        return result.as_dict()

    @app.get("/messages", response_model=list[MessageResponse])
    async def get_messages() -> list[dict[str, object]]:
        session = database.get_active_session()
        if session is None:
            return []
        notes_by_message: dict[int, dict[str, object]] = {}
        for note in database.list_voice_notes():
            message_id = note["message_id"]
            if message_id is not None:
                notes_by_message.setdefault(int(message_id), note)

        result: list[dict[str, object]] = []
        for message in database.list_messages(int(session["id"])):
            if message["role"] == "event":
                continue
            restored_note = database.restored_voice_note(message)
            note = notes_by_message.get(int(message["id"]))
            ooc = None
            if restored_note is None and note is not None and note["overturned_at"] is None:
                ooc = {
                    "original": note["original"],
                    "reason": note["reason"],
                    "rewritten": note["rewritten"],
                    "voice_note_id": note["id"],
                    "source": note["source"],
                }
            result.append(
                {
                    "message_id": message["id"],
                    "role": message["role"],
                    "content": restored_note["original"] if restored_note else message["content"],
                    "created_at": message["created_at"],
                    "ooc": ooc,
                }
            )
        return result

    @app.post("/ooc/flag", response_model=OOCFlagResponse)
    async def flag_ooc(request: OOCFlagRequest) -> dict[str, object]:
        try:
            result = await gatekeeper_service.flag_message(
                request.message_id,
                request.player_rewrite,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "verdict": result.verdict,
            "reason": result.reason,
            "rewritten": result.rewritten,
            "voice_note_id": result.voice_note_id,
        }

    @app.get("/voice-notes")
    async def get_voice_notes() -> list[dict[str, object]]:
        return database.list_voice_notes()

    @app.patch("/voice-notes/{note_id}")
    async def patch_voice_note(note_id: int, request: VoiceNotePatch) -> dict[str, object]:
        if not database.update_voice_note_active(note_id, request.active):
            raise HTTPException(status_code=404, detail="找不到这条勘误")
        return {"id": note_id, "active": request.active}

    @app.post("/voice-notes/{note_id}/overturn", response_model=OverturnResponse)
    async def overturn_voice_note(note_id: int, request: OverturnRequest) -> dict[str, object]:
        result = database.overturn_voice_note(note_id, request.note)
        if result is None:
            raise HTTPException(status_code=404, detail="找不到这条勘误")
        return result

    @app.get("/export")
    async def get_export(format: str = "json") -> Response:
        if format == "markdown":
            return Response(
                build_markdown_export(
                    database,
                    character_name=config.character.name,
                ),
                media_type="text/markdown; charset=utf-8",
                headers={
                    "Content-Disposition": 'attachment; filename="yuwen-export.md"'
                },
            )
        if format != "json":
            raise HTTPException(status_code=422, detail="不支持的导出格式")
        return JSONResponse(
            build_export(database),
            headers={"Content-Disposition": 'attachment; filename="yuwen-export.json"'},
        )

    @app.get("/health")
    async def get_health() -> dict[str, object]:
        return {"ok": True, "schema_version": database.schema_version()}

    return app


def run() -> None:
    """从当前目录的配置启动服务。"""

    import uvicorn

    config = load_config(os.environ.get("YUWEN_CONFIG", "config.toml"))
    uvicorn.run(
        create_app(config),
        host=config.server.host,
        port=config.server.port,
    )
