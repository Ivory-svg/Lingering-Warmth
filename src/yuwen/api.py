"""余温的 FastAPI 入口。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextlib import suppress
from importlib.resources import files
import os

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from yuwen.chat import ChatModel, ChatService
from yuwen.config import AppConfig, load_config
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
    )
    inflow_service = InflowService(
        database,
        model,
        character_name=config.character.name,
        player_name=config.character.player_name,
        every_n_turns=config.inflow.every_n_turns,
        embedder=embedder,
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

    @asynccontextmanager
    async def lifespan(_: FastAPI):
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

    @app.get("/", response_class=HTMLResponse)
    async def get_index() -> str:
        return files("yuwen.static").joinpath("index.html").read_text(encoding="utf-8")

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
            raise HTTPException(status_code=404, detail="找不到这条语感笔记")
        return {"id": note_id, "active": request.active}

    @app.post("/voice-notes/{note_id}/overturn", response_model=OverturnResponse)
    async def overturn_voice_note(note_id: int, request: OverturnRequest) -> dict[str, object]:
        result = database.overturn_voice_note(note_id, request.note)
        if result is None:
            raise HTTPException(status_code=404, detail="找不到这条语感笔记")
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
