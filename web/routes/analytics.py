"""Usage analytics endpoints — event recording and dashboard."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from notebook.db import get_conn
from pydantic import BaseModel, Field
from routes.auth import get_current_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["analytics"])


class UsageEvent(BaseModel):
    event_type: str = Field(..., pattern=r"^(query|tool_use|upload|chat|export)$")
    event_data: dict | None = None
    route: str | None = None
    tools_used: list[str] | None = None
    latency_ms: float | None = None


def log_usage_event(
    event_type: str,
    event_data: dict | None = None,
    route: str | None = None,
    tools_used: list[str] | None = None,
    latency_ms: float | None = None,
    user_id: str | None = None,
) -> str:
    """Helper to log a usage event from anywhere in the app."""
    event_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO usage_events (id, user_id, event_type, event_data, route, tools_used, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (event_id, user_id, event_type, json.dumps(event_data) if event_data else None,
              route, json.dumps(tools_used) if tools_used else None, latency_ms))
    return event_id


@router.post("/analytics/event")
async def record_usage_event(body: UsageEvent, _user: dict = Depends(get_current_user)):
    """Record a usage event."""
    event_id = log_usage_event(
        event_type=body.event_type,
        event_data=body.event_data,
        route=body.route,
        tools_used=body.tools_used,
        latency_ms=body.latency_ms,
        user_id=_user["id"],
    )
    return {"id": event_id}


@router.get("/analytics/dashboard")
async def get_analytics_dashboard(days: int = Query(default=30, ge=1, le=365), _user: dict = Depends(get_current_user)):
    """Get analytics dashboard data."""
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    uid = _user["id"]

    with get_conn() as conn:
        events_by_type = conn.execute("""
            SELECT event_type, COUNT(*) as count FROM usage_events
            WHERE created_at >= ? AND user_id = ? GROUP BY event_type
        """, (since, uid)).fetchall()

        events_per_day = conn.execute("""
            SELECT date(created_at) as day, COUNT(*) as count FROM usage_events
            WHERE created_at >= ? AND user_id = ? GROUP BY day ORDER BY day
        """, (since, uid)).fetchall()

        route_dist = conn.execute("""
            SELECT route, COUNT(*) as count FROM usage_events
            WHERE created_at >= ? AND user_id = ? AND route IS NOT NULL GROUP BY route
        """, (since, uid)).fetchall()

        # Aggregate tool counts in SQL via json_each to avoid loading all raw
        # event rows into Python (which is unbounded for large date windows).
        tool_rows = conn.execute("""
            SELECT t.value as tool, COUNT(*) as count
            FROM usage_events, json_each(tools_used) t
            WHERE created_at >= ? AND user_id = ? AND tools_used IS NOT NULL
            GROUP BY t.value
            ORDER BY count DESC
            LIMIT 100
        """, (since, uid)).fetchall()

        tool_counts: dict[str, int] = {r["tool"]: r["count"] for r in tool_rows}

        avg_latency = conn.execute("""
            SELECT AVG(latency_ms) as avg_ms FROM usage_events
            WHERE created_at >= ? AND user_id = ? AND latency_ms IS NOT NULL
        """, (since, uid)).fetchone()

        total = conn.execute("""
            SELECT COUNT(*) as total FROM usage_events WHERE created_at >= ? AND user_id = ?
        """, (since, uid)).fetchone()

    return {
        "period_days": days,
        "total_events": total["total"] if total else 0,
        "events_by_type": {r["event_type"]: r["count"] for r in events_by_type},
        "events_per_day": [{"day": r["day"], "count": r["count"]} for r in events_per_day],
        "route_distribution": {r["route"]: r["count"] for r in route_dist},
        "tool_usage": tool_counts,
        "avg_latency_ms": round(avg_latency["avg_ms"], 2) if avg_latency and avg_latency["avg_ms"] else None,
    }
