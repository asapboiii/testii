from dotenv import load_dotenv
load_dotenv()

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from api import create_router
from engine import VisionEngine
from vault_utils import DB_PATH

import os
_src = os.getenv("VIDEO_SOURCE", "0")
engine = VisionEngine(source=int(_src) if _src.isdigit() else _src)


@asynccontextmanager
async def lifespan(app):
    engine.start()
    yield
    engine.stop()


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware,
                   allow_origins=["http://localhost:5173"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(create_router(engine))
Path("clips").mkdir(exist_ok=True)
Path("snapshots").mkdir(exist_ok=True)
app.mount("/clips", StaticFiles(directory="clips"), name="clips")
app.mount("/snapshots", StaticFiles(directory="snapshots"), name="snapshots")


def mjpeg():
    while True:
        jpg = engine.get_jpeg()
        if jpg is None:
            time.sleep(0.05)
            continue
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
               + jpg + b"\r\n")
        time.sleep(0.04)


@app.get("/video")
def video():
    return StreamingResponse(
        mjpeg(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/status")
def status():
    return engine.get_status()


@app.get("/events")
def events(limit: int = 50):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?",
                       (limit,)).fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        d["clip_url"] = (f"/clips/{Path(d['clip_path']).name}"
                         if d["clip_path"] else None)
        d["snapshot_url"] = (f"/snapshots/{Path(d['snapshot_path']).name}"
                             if d["snapshot_path"] else None)
        out.append(d)
    return out