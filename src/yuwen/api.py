"""余温的 FastAPI 入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from importlib.resources import files
import os

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from yuwen.chat import ChatModel, ChatService
from yuwen.config import AppConfig, load_config
from yuwen.context import ContextBuilder
from yuwen.export import build_export
from yuwen.gatekeeper import GatekeeperService
from yuwen.handoff import HandoffService
from yuwen.inflow import InflowService
from yuwen.llm import LLMClient
from yuwen.store import Store
from yuwen.worldbook import WorldbookReader


class ChatRequest(BaseModel):
    content: str


class ChatResponse(BaseModel):
    message_id: int
    reply: str
    session_id: int
    ooc_rewritten: bool
    handoff_happened: bool


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


def create_app(
    config: AppConfig,
    *,
    store: Store | None = None,
    llm: ChatModel | None = None,
    worldbook: WorldbookReader | None = None,
) -> FastAPI:
    database = store or Store(config.storage.db_path)
    model = llm or LLMClient(config.llm)
    books = worldbook or WorldbookReader(config.character)
    context = ContextBuilder(
        database,
        books,
        voice_notes_limit=config.gatekeeper.voice_notes_in_context,
    )
    handoff_service = HandoffService(
        database,
        model,
        context_limit=config.llm.context_limit,
        trigger_ratio=config.handoff.trigger_ratio,
        raw_tail=config.handoff.raw_tail,
    )
    gatekeeper_service = GatekeeperService(database, books, model)
    chat_service = ChatService(
        database,
        context,
        model,
        handoff=handoff_service,
        gatekeeper=gatekeeper_service if config.gatekeeper.pre_send else None,
    )
    inflow_service = InflowService(database, model)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        yield
        if isinstance(model, LLMClient):
            await model.aclose()

    app = FastAPI(title="余温", version="0.1.0", lifespan=lifespan)
    app.state.config = config
    app.state.store = database
    app.state.chat_service = chat_service
    app.state.inflow_service = inflow_service
    app.state.handoff_service = handoff_service
    app.state.gatekeeper_service = gatekeeper_service

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
        background_tasks.add_task(
            inflow_service.process_turn,
            result.user_message_id,
            result.message_id,
        )
        return result.as_dict()

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

    @app.get("/export")
    async def get_export() -> JSONResponse:
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
