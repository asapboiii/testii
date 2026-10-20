import os, sqlite3, time, threading
from collections import deque
from pathlib import Path
import cv2
import requests
import subprocess
import imageio_ffmpeg

CAMERA_NAME = "Camera #1: Front Door"
DB_PATH = "vault.db"
CLIP_DIR = Path("clips"); CLIP_DIR.mkdir(exist_ok=True)
SNAP_DIR = Path("snapshots"); SNAP_DIR.mkdir(exist_ok=True)


# ---------- event log ----------
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL, camera TEXT, track_id INTEGER, identity TEXT,
        event_type TEXT, clip_path TEXT, snapshot_path TEXT)""")
    # review fields used by the frontend (added later, so migrate old databases)
    have = {r[1] for r in con.execute("PRAGMA table_info(events)")}
    for col, ddl in (("status", "TEXT DEFAULT 'new'"), ("notes", "TEXT"),
                     ("reviewed_at", "REAL")):
        if col not in have:
            con.execute(f"ALTER TABLE events ADD COLUMN {col} {ddl}")
    con.commit()
    con.close()

def log_event(camera, track_id, identity, event_type,
              clip_path=None, snapshot_path=None):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO events (ts, camera, track_id, identity, event_type,"
        " clip_path, snapshot_path) VALUES (?,?,?,?,?,?,?)",
        (time.time(), camera, track_id, identity, event_type,
         clip_path, snapshot_path))
    con.commit()
    con.close()


def _load_env():
    # 1. Try python-dotenv if installed
    try:
        from dotenv import load_dotenv
        env_file = Path(__file__).resolve().parent / ".env"
        if env_file.exists():
            load_dotenv(dotenv_path=env_file)
        else:
            load_dotenv()
    except Exception:
        pass

    # 2. Manual fallback parser in case python-dotenv is not installed
    candidates = [
        Path(__file__).resolve().parent / ".env",
        Path.cwd() / ".env"
    ]
    for env_path in candidates:
        if env_path.exists():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip("'\"")
                        if k and not os.getenv(k):
                            os.environ[k] = v
            except Exception:
                pass

_load_env()


# ---------- telegram ----------
def _send(text, photo_path):
    token, chat = os.getenv("TG_TOKEN"), os.getenv("TG_CHAT_ID")
    if not token or not chat:
        _load_env()
        token, chat = os.getenv("TG_TOKEN"), os.getenv("TG_CHAT_ID")
    if not token or not chat:
        print("[telegram] TG_TOKEN / TG_CHAT_ID not set, skipping")
        return
    try:
        base = f"https://api.telegram.org/bot{token}"
        if photo_path:
            with open(photo_path, "rb") as f:
                requests.post(f"{base}/sendPhoto",
                              data={"chat_id": chat, "caption": text},
                              files={"photo": f}, timeout=15)
        else:
            requests.post(f"{base}/sendMessage",
                          data={"chat_id": chat, "text": text}, timeout=15)
    except Exception as e:
        print("[telegram] failed:", e)

def send_telegram(text, photo_path=None):
    # background thread so the video loop never freezes
    threading.Thread(target=_send, args=(text, photo_path), daemon=True).start()

def convert_to_h264(path):
    def _run():
        tmp = path + ".h264.mp4"
        try:
            subprocess.run(
                [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", path,
                 "-vcodec", "libx264", "-pix_fmt", "yuv420p",
                 "-preset", "veryfast", "-movflags", "+faststart", tmp],
                check=True, capture_output=True)
            os.replace(tmp, path)
        except Exception as e:
            print("[ffmpeg] conversion failed:", e)
    threading.Thread(target=_run, daemon=True).start()

# ---------- clip recorder (pre-roll + post-roll) ----------
class ClipRecorder:
    def __init__(self, fps=20, pre_sec=5, post_sec=5):
        self.fps = fps
        self.buffer = deque(maxlen=fps * pre_sec)
        self.post_frames = fps * post_sec
        self.writer = None
        self.remaining = 0

    def add(self, frame):
        if self.writer is not None:
            self.writer.write(frame)
            self.remaining -= 1
            if self.remaining <= 0:
                self.writer.release()
                self.writer = None
                convert_to_h264(self.path)
        else:
            self.buffer.append(frame.copy())

    def trigger(self, path):
        if self.writer is not None:        # already recording: extend it
            self.remaining = self.post_frames
            return
        if not self.buffer:
            return
        h, w = self.buffer[-1].shape[:2]
        self.path = path
        self.writer = cv2.VideoWriter(
            path, cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (w, h))
        for f in self.buffer:
            self.writer.write(f)
        self.buffer.clear()
        self.remaining = self.post_frames