"""REST + WebSocket API for the `frontend/` dashboard (contract in frontend/README.md).

Maps Vision Vault's own data (events table, single webcam) onto the frontend's
cameras / incidents / dashboard model. Auth is not implemented: run the
frontend with VITE_AUTH_ENABLED=false.
"""
import asyncio
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import (APIRouter, HTTPException, Query, Request, WebSocket,
                     WebSocketDisconnect)
from pydantic import BaseModel

from vault_utils import DB_PATH

CAMERA_ID = "cam-01"
STATUSES = {"new", "under_review", "confirmed", "dismissed"}


class StatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None


def _rows(sql, args=()):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _iso(ts):
    return (datetime.fromtimestamp(ts, timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"))


def _local_day(ts):
    return datetime.fromtimestamp(ts).date()


def _incident(r, base):
    name = r["camera"] or ""
    location = name.split(": ", 1)[-1]
    clip = f"{base}/clips/{Path(r['clip_path']).name}" if r["clip_path"] else None
    snap = (f"{base}/snapshots/{Path(r['snapshot_path']).name}"
            if r["snapshot_path"] else None)
    out = {"id": str(r["id"]), "cameraId": CAMERA_ID, "cameraName": name,
           "location": location, "timestamp": _iso(r["ts"]),
           "label": r["event_type"] or "intrusion",
           "confidence": 1.0,        # rule-based alert, no model score is logged
           "status": r["status"] or "new", "detections": []}
    if snap:
        out["snapshotUrl"] = snap
    if clip:
        out["clipUrl"] = clip
    if r["notes"]:
        out["notes"] = r["notes"]
    if r["reviewed_at"]:
        out["reviewedBy"] = "Dashboard user"
        out["reviewedAt"] = _iso(r["reviewed_at"])
    return out


def create_router(engine):
    router = APIRouter(prefix="/api")

    def base_url(request: Request):
        return str(request.base_url).rstrip("/")

    # ---------- cameras (single webcam / video source) ----------
    def camera(base):
        return {"id": CAMERA_ID, "name": engine.camera_name,
                "location": engine.camera_name.split(": ", 1)[-1],
                "streamUrl": f"{base}/video",
                "status": "online" if engine.running else "offline",
                "lastSeen": _iso(datetime.now().timestamp())}

    @router.get("/cameras")
    def cameras(request: Request):
        return [camera(base_url(request))]

    @router.post("/cameras")
    @router.put("/cameras/{cam_id}")
    @router.delete("/cameras/{cam_id}")
    def camera_write(cam_id: str = ""):
        raise HTTPException(501, "Camera management is not supported by this backend.")

    # ---------- incidents ----------
    @router.get("/incidents")
    def incidents(request: Request, search: str = "", cameraId: str = "",
                  status: str = "", sort: str = "newest",
                  from_: Optional[str] = Query(None, alias="from"),
                  to: Optional[str] = None):
        base = base_url(request)
        out = [_incident(r, base) for r in _rows("SELECT * FROM events")]
        s = search.strip().lower()
        if s:
            out = [i for i in out if any(
                s in str(i[k]).lower()
                for k in ("id", "cameraName", "location", "label"))]
        if cameraId:
            out = [i for i in out if i["cameraId"] == cameraId]
        if status:
            out = [i for i in out if i["status"] == status]
        if from_:
            out = [i for i in out if i["timestamp"][:10] >= from_]
        if to:
            out = [i for i in out if i["timestamp"][:10] <= to]
        out.sort(key=lambda i: i["timestamp"], reverse=(sort != "oldest"))
        return out

    @router.get("/incidents/{inc_id}")
    def incident(inc_id: int, request: Request):
        rows = _rows("SELECT * FROM events WHERE id=?", (inc_id,))
        if not rows:
            raise HTTPException(404, f"Incident {inc_id} was not found.")
        return _incident(rows[0], base_url(request))

    @router.patch("/incidents/{inc_id}")
    def review(inc_id: int, body: StatusUpdate, request: Request):
        if body.status not in STATUSES:
            raise HTTPException(422, f"Invalid status '{body.status}'.")
        con = sqlite3.connect(DB_PATH)
        cur = con.execute(
            "UPDATE events SET status=?, notes=?, reviewed_at=strftime('%s','now')"
            " WHERE id=?", (body.status, body.notes, inc_id))
        con.commit()
        con.close()
        if cur.rowcount == 0:
            raise HTTPException(404, "Incident not found.")
        return _incident(_rows("SELECT * FROM events WHERE id=?", (inc_id,))[0],
                         base_url(request))

    # ---------- dashboard ----------
    @router.get("/dashboard/summary")
    def summary():
        rows = _rows("SELECT ts, status FROM events")
        today = date.today()
        return {"activeCameras": 1 if engine.running else 0, "totalCameras": 1,
                "incidentsToday": sum(_local_day(r["ts"]) == today for r in rows),
                "totalIncidents": len(rows),
                "unresolvedAlerts": sum((r["status"] or "new") in ("new", "under_review")
                                        for r in rows)}

    @router.get("/dashboard/trend")
    def trend(days: int = 14):
        rows = _rows("SELECT ts, status FROM events")
        today = date.today()
        points = []
        for d in range(days - 1, -1, -1):
            day = today - timedelta(days=d)
            same = [r for r in rows if _local_day(r["ts"]) == day]
            confirmed = sum(r["status"] == "confirmed" for r in same)
            points.append({"date": day.isoformat(),
                           "potential": len(same) - confirmed,
                           "confirmed": confirmed})
        return points

    # ---------- real-time: new intrusions + camera status ----------
    @router.websocket("/ws/events")
    async def ws_events(ws: WebSocket):
        await ws.accept()
        base = str(ws.base_url).rstrip("/")
        last = (_rows("SELECT COALESCE(MAX(id),0) AS m FROM events")[0]["m"])
        online = None
        try:
            while True:
                for r in _rows("SELECT * FROM events WHERE id>? ORDER BY id", (last,)):
                    last = r["id"]
                    await ws.send_json({"type": "alert", "data": _incident(r, base)})
                if online != engine.running:
                    online = engine.running
                    await ws.send_json({"type": "camera_status", "data": {
                        "cameraId": CAMERA_ID,
                        "status": "online" if online else "offline"}})
                await asyncio.sleep(1)
        except (WebSocketDisconnect, RuntimeError):
            pass

    return router
