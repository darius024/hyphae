# Hyphae — Features

This document lists the features implemented in the Hyphae research copilot application. It focuses on user-facing functionality, developer-facing APIs, and notable architectural/privacy capabilities.

## Core goals

- Privacy-first hybrid research assistant: run retrieval and core reasoning on-device when possible, and fall back to cloud only for opt-in abstract tasks (hypothesis generation, literature search).
- Local-first data storage: user corpus and notebooks are stored locally (SQLite + local embeddings). Raw experimental data never leaves the machine.
- Lightweight, extensible tool-based architecture: research tasks are implemented as discrete tools which the routing engine dispatches to.

## High-level features

- Natural-language research chat: conversational interface grounded in your uploaded documents.
- Notebook workspaces: create isolated notebooks to collect sources and conversations for focused research projects.
- Document ingestion: upload PDFs, text, Markdown, CSV, JSON, and URLs. Files are ingested and indexed for retrieval.
- Vector search + retrieval: FAISS-based retrieval with sentence-transformer embeddings for accurate context selection.
- FunctionGemma on-device model: run a 270M FunctionGemma model locally via the Cactus SDK for many queries.
- Gemini cloud integration: optional cloud fallback (Gemini) for high-level synthesis when explicitly requested.

## Notebooks & workspace features

- Create / list / rename / delete notebooks via the UI and API.
- Notebook-level sources: upload files or add URLs to a notebook; each source has a preview, raw download and sensitivity tag.
- Conversations per notebook: multiple conversation threads, rename/delete support, history persisted locally.
- Source management: preview text, download original file, remove source.
- Notebook settings: per-notebook options stored in the DB.

## Collaboration & Organization (Team features)

- Organizations: create organizations, manage members, and share notebooks with an org.
- Invites: invite team members via email with role assignment and token-based accept flow.
- Org roles: owner / admin / member / viewer roles with member management endpoints.
- Activity feed: log important actions (notebook created/shared/edited, invites, comments) and display activity per-org or per-notebook.

## Comments & Review

- Comment threads: create, reply, update, resolve, and delete comments tied to notebooks and sources (API endpoints implemented).
- Inline comment support (UI wiring in progress): functions for loading and creating comments exist; inline UI to attach threads to specific content is planned.

## Research tools (available toolset)

The application exposes a set of named tools that the UI or routing engine can call. Each tool declares whether it runs locally or in the cloud.

- search_papers (LOCAL): search the local corpus for relevant passages.
- summarise_notes (LOCAL): summarise notebook sources and highlight gaps.
- create_note (LOCAL): save a new note to the local corpus.
- list_documents (LOCAL): list indexed files and metadata.
- compare_documents (LOCAL): compare two documents and surface differences with citations.
- read_document (LOCAL): open and read a document (truncated preview).
- search_text (LOCAL): keyword search with paragraph snippets.
- generate_hypothesis (CLOUD): propose testable hypotheses (Gemini fallback).
- search_literature (CLOUD): query external literature and return summarized findings.

## UI capabilities

- Single-page web UI (vanilla JS) with:
  - Sidebar corpus and notebooks panel
  - Modals for upload, tags, versions, analytics, graph, and organizations
  - Keyboard shortcuts (Cmd/Ctrl+K, /, Esc, Cmd/Ctrl+Enter)
  - Quick prompts and research starter templates
- Document previewer with in-browser PDF rendering and text preview
- Drag-and-drop upload and progress feedback
- Visual badges showing whether an answer used LOCAL or CLOUD processing
- Privacy audit log UI showing query timestamps, routing decisions, and tools used

## Privacy, sanitisation and audit

- Sanitisation: messages sent to cloud are sanitised (file paths, PII, measurements) to reduce accidental data leakage.
- Audit log: every query is recorded with timestamp, tools used, local/cloud flag, and latency.
- Document sensitivity tags: mark documents as Confidential or Shareable and enforce UI visibility.

## Analytics, Graph & Versions

- Usage analytics: basic activity counters and recent activity feed in the UI.
- Knowledge graph / document linking: visual graph of document relationships and backlinks (modal + canvas).
- Version history: view previous snapshots for notebook content and sources (modal listing).

## Voice & multimedia

- Voice input: record voice in the browser, transcribe using Whisper via Cactus, then query the corpus.

## API & CLI

- Full REST API for most app functionality (querying, notebooks, sources, auth, tools, privacy-log).
- Auth endpoints (signup/login/logout/me) using bearer sessions stored in `sessions` table.
- CLI for lightweight interaction, ingestion and batch operations.

## Developer & deployment notes

- Backend: FastAPI + Uvicorn. Application code is in `web/`.
- Local DB: SQLite (`web/notebook.db`) containing notebooks, sources, conversations, comments, organizations, sessions.
- Model runtime: Cactus SDK submodule included in `cactus/` for on-device FunctionGemma execution.
- Tests: pytest-based tests in `tests/` covering ingestion, privacy, DB, and API endpoints.

## Limitations & TODOs

- Inline comments UI (per-source/note inline thread attachments) — frontend wiring is partially implemented; inline UI still requires polishing.
- Some UX edge-cases around session expiration and clearer sign-in prompts can be improved.
- Larger-scale multi-user workflows (e.g., concurrent editing, real-time presence) are not implemented.

## Where to find things

- App entry: `web/app.py`
- Notebooks & DB schema: `web/notebook/db.py`
- API routes: `web/routes/` (notebooks, query, auth, corpus, features)
- Frontend: `web/static/index.html`, `web/static/app.js`, `web/static/style.css`
- On-device model integration: `cactus/` submodule
