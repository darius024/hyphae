"""
Pydantic v2 schemas for the Notebook API layer.
Gemini + Cactus only — no OpenAI references.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


def _uid() -> str:
    return str(uuid.uuid4())


# ── Notebooks ────────────────────────────────────────────────────────────

class NotebookCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    allow_cloud: bool = False


class NotebookUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    allow_cloud: bool | None = None


class Notebook(BaseModel):
    id: str
    name: str
    description: str | None = None
    allow_cloud: bool
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


# ── Sources ───────────────────────────────────────────────────────────────

SourceStatus = Literal["pending", "processing", "done", "failed"]
SourceType   = Literal["pdf", "txt", "md", "url"]


class SourceResponse(BaseModel):
    id: str
    notebook_id: str
    title: str | None = None
    type: str
    filename: str | None = None
    url: str | None = None
    page_count: int | None = None
    status: SourceStatus
    error: str | None = None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class UploadResponse(BaseModel):
    source_id: str
    filename: str
    status: SourceStatus


# ── Citations ─────────────────────────────────────────────────────────────

class Citation(BaseModel):
    number: int
    source_id: str
    source_title: str | None = None
    page_number: int | None = None
    snippet: str
    chunk_id: str | None = None
    score: float | None = None


# ── Conversations ─────────────────────────────────────────────────────────

class ConversationCreate(BaseModel):
    title: str | None = None


class Conversation(BaseModel):
    id: str
    notebook_id: str
    title: str | None = None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


# ── Messages ──────────────────────────────────────────────────────────────

class Message(BaseModel):
    id: str
    conversation_id: str
    notebook_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    citations: list[Citation] | None = None
    source: str | None = None
    latency_ms: float | None = None
    created_at: str

    model_config = {"from_attributes": True}


# ── Chat request/response ─────────────────────────────────────────────────

class ChatMsg(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMsg]
    conversation_id: str | None = None
    stream: bool = False


class ChatResponse(BaseModel):
    message: Message
    conversation_id: str


# ── SSE streaming chunk ───────────────────────────────────────────────────

class StreamChunk(BaseModel):
    type: Literal["token", "citations", "meta", "error", "done"]
    data: Any = None


# ── Settings ──────────────────────────────────────────────────────────────

class NbSettings(BaseModel):
    embed_model: str
    retrieval_top_k: int
    chunk_size: int
    chunk_overlap: int


class SettingUpdate(BaseModel):
    value: str


# ── Voice ────────────────────────────────────────────────────────────────

class VoiceResponse(BaseModel):
    transcript: str
