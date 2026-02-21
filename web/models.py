"""
Pydantic v2 schemas for the Notebook API layer.
Gemini + Cactus only — no OpenAI references.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


def _uid() -> str:
    return str(uuid.uuid4())


# ── Notebooks ────────────────────────────────────────────────────────────

class NotebookCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    allow_cloud: bool = False


class NotebookUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    allow_cloud: Optional[bool] = None


class Notebook(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
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
    title: Optional[str] = None
    type: str
    filename: Optional[str] = None
    url: Optional[str] = None
    page_count: Optional[int] = None
    status: SourceStatus
    error: Optional[str] = None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class UploadResponse(BaseModel):
    source_id: str
    filename: str
    status: SourceStatus


# ── Citations ─────────────────────────────────────────────────────────────

class Citation(BaseModel):
    chunk_id: str
    source_id: str
    source_title: Optional[str] = None
    page: Optional[int] = None
    snippet: str
    score: float


# ── Conversations ─────────────────────────────────────────────────────────

class ConversationCreate(BaseModel):
    title: Optional[str] = None


class Conversation(BaseModel):
    id: str
    notebook_id: str
    title: Optional[str] = None
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
    citations: Optional[List[Citation]] = None
    source: Optional[str] = None
    latency_ms: Optional[float] = None
    created_at: str

    model_config = {"from_attributes": True}


# ── Chat request/response ─────────────────────────────────────────────────

class ChatMsg(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMsg]
    conversation_id: Optional[str] = None
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
