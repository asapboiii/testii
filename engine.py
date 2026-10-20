import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import pickle
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO
from insightface.app import FaceAnalysis

from vault_utils import (CAMERA_NAME, CLIP_DIR, SNAP_DIR, ClipRecorder,
                         init_db, log_event, send_telegram)

VERIFY_INTERVAL = 30      # frames between re-checks of an already-recognized person
VERIFY_FAILS = 2          # consecutive mismatches before the lock is released

GRACE = 3.0               # seconds an unknown must be seen before alerting
TG_COOLDOWN = 15.0        # min seconds between Telegram messages
FACE_REC_INTERVAL = 8     # frames between face-recognition attempts per person
CLIP_FPS = 20             # set to your real FPS (shown on screen) so clips play at normal speed
IMG_EXT = {".jpg", ".jpeg", ".png"}

PERSON_CONF = 0.55       # ignore weak "person" detections
MIN_TRACK_FRAMES = 10    # a track must exist this many frames before it counts
MOVE_REQUIRED = 0.08     # fraction of frame width an UNKNOWN must travel before it can alert
MIN_FACE_PX = 40         # ignore faces smaller than this (garbage embeddings)
MIN_DET_SCORE = 0.6      # ignore low-confidence face detections
MATCH_THRESH = 0.4


class VisionEngine:
    def __init__(self, source=0, camera_name=CAMERA_NAME):
        self.source = source
        self.is_file = isinstance(source, str)
        self.camera_name = camera_name

        # Lightweight detector and CPU-optimized face analyzer
        self.detector = YOLO("yolov8n.pt")
        self.face_app = FaceAnalysis(name="buffalo_s")
        self.face_app.prepare(ctx_id=-1, det_size=(320, 320))

        self.faces_path = Path("faces")
        self.cache_file = self.faces_path / "embeddings.pkl"
        self.known = self._load_known()

        self.recorder = ClipRecorder(fps=CLIP_FPS)
        self.identity = {}        # track_id -> name (locked once recognized)
        self.last_faces = {}      # track_id -> [((x1,y1,x2,y2), name, score)]
        self.last_checked = {}    # track_id -> frame_count of last face check
        self.verify_fail = {}     # track_id -> consecutive re-check mismatches
        self.unknown_since = {}   # track_id -> first time seen as unknown
        self.alerted = set()
        self.last_tg = 0.0

        self.lock = threading.Lock()
        self.latest_jpeg = None
        self.unknown_count = 0
        self.fps = 0.0
        self.running = False
        self.thread = None

        self.track_age = {}      # track_id -> frames seen
        self.first_pos = {}      # track_id -> first (cx, cy)
        self.moved = set()       # track_ids that have actually moved


    # ---------- enrollment (with disk caching) ----------
    def _load_known(self):
        self.faces_path.mkdir(parents=True, exist_ok=True)
        known = {}

        if self.cache_file.exists():
            try:
                with open(self.cache_file, "rb") as f:
                    known = pickle.load(f)
                print("Loaded cached embeddings for:", list(known.keys()))
            except Exception as e:
                print("Cache corrupted or unreadable, recomputing:", e)
                known = {}

        if not known:
            for person_dir in self.faces_path.iterdir():
                if not person_dir.is_dir():
                    continue
                embs = []
                for img_path in person_dir.iterdir():
                    if img_path.suffix.lower() not in IMG_EXT:
                        continue
                    img = cv2.imread(str(img_path))
                    if img is None:
                        continue
                    h, w = img.shape[:2]
                    if max(h, w) > 1080:
                        scale = 1080 / max(h, w)
                        img = cv2.resize(img, (int(w * scale), int(h * scale)))
                    detected = self.face_app.get(img)
                    if detected:
                        best = max(detected, key=lambda f:
                                   (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
                        embs.append(best.normed_embedding)
                if embs:
                    known[person_dir.name] = np.stack(embs)   # keep every photo

            if known:
                with open(self.cache_file, "wb") as f:
                    pickle.dump(known, f)
                print("Enrolled and saved cache for:", list(known.keys()))
            else:
                print("No faces enrolled. Check the faces/ folder and photo formats.")
        return known

    def _identify(self, emb, thresh=MATCH_THRESH):
        best, best_score = "unknown", 0.0
        for name, refs in self.known.items():
            score = float(np.max(refs @ emb))
            if score > best_score:
                best, best_score = name, score
        return (best if best_score > thresh else "unknown"), best_score

    # ---------- control ----------
    def start(self):
        init_db()
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)

    def get_jpeg(self):
        with self.lock:
            return self.latest_jpeg

    def get_status(self):
        with self.lock:
            return {"camera": self.camera_name,
                    "running": self.running,
                    "unknown_count": self.unknown_count,
                    "fps": round(self.fps, 1)}

    # ---------- main loop ----------
    def _loop(self):
        if self.is_file:
            cap = cv2.VideoCapture(self.source)
        else:
            # Windows' default MSMF backend can open a webcam yet never deliver frames; DirectShow is reliable
            backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
            cap = cv2.VideoCapture(self.source, backend)
        if not cap.isOpened():
            print(f"[camera] could not open video source {self.source!r}")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        failed_reads = 0

        frame_count = 0
        fps = 0.0
        prev_time = time.time()

        src_fps = cap.get(cv2.CAP_PROP_FPS) or 25
        frame_delay = 1.0 / src_fps

        while self.running:
            ok, frame = cap.read()
            if not ok:
                if self.is_file:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)   # loop the video
                    continue
                failed_reads += 1
                if failed_reads % 100 == 1:
                    print(f"[camera] no frames from source {self.source!r} "
                          "(in use by another app, or try VIDEO_SOURCE=1 in .env)")
                time.sleep(0.05)
                continue
            failed_reads = 0

            # keep processing size the same as the webcam (video files are often 1080p)
            h0, w0 = frame.shape[:2]
            if w0 > 640:
                frame = cv2.resize(frame, (640, int(h0 * 640 / w0)))

            current_unknown = set()
            pending = []              # intrusions to trigger after drawing
            now = time.time()
            frame_count += 1
            frame_h, frame_w = frame.shape[:2]

            res = self.detector.track(frame, classes=[0], conf=PERSON_CONF,
                                      persist=True, tracker="bytetrack.yaml",
                                      verbose=False)[0]

            if res.boxes.id is not None:
                boxes = res.boxes.xyxy.cpu().numpy().astype(int)
                ids = res.boxes.id.int().cpu().tolist()

                for (x1, y1, x2, y2), tid in zip(boxes, ids):
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(frame_w, x2), min(frame_h, y2)
                    person_w, person_h = x2 - x1, y2 - y1
                    if person_w < 20 or person_h < 20:
                        continue
                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                    fx, fy = self.first_pos.setdefault(tid, (cx, cy))
                    if abs(cx - fx) + abs(cy - fy) > MOVE_REQUIRED * frame_w:
                        self.moved.add(tid)
                    self.track_age[tid] = self.track_age.get(tid, 0) + 1
                    if self.track_age[tid] < MIN_TRACK_FRAMES:
                        continue

                    current_name = self.identity.get(tid, "unknown")
                    interval = (FACE_REC_INTERVAL if current_name == "unknown"
                                else VERIFY_INTERVAL)
                    since_check = frame_count - self.last_checked.get(tid, -999)

                    if since_check >= interval:
                        self.last_checked[tid] = frame_count

                        # head & shoulders only (top 55% of the person)
                        head_y2 = min(frame_h, y1 + int(person_h * 0.55))
                        head_crop = frame[y1:head_y2, x1:x2]

                        if head_crop.size > 0:
                            detected_faces = self.face_app.get(head_crop)
                            detected_faces = [d for d in detected_faces
                                              if d.det_score >= MIN_DET_SCORE
                                              and (d.bbox[2] - d.bbox[0]) >= MIN_FACE_PX]
                            if detected_faces:
                                # one face per person: nearest to the head anchor
                                ax, ay = person_w / 2, person_h * 0.15
                                f = min(detected_faces, key=lambda d:
                                        (((d.bbox[0] + d.bbox[2]) / 2 - ax) ** 2 +
                                         ((d.bbox[1] + d.bbox[3]) / 2 - ay) ** 2))
                                box = (int(f.bbox[0] + x1), int(f.bbox[1] + y1),
                                       int(f.bbox[2] + x1), int(f.bbox[3] + y1))
                                n, s = self._identify(f.normed_embedding)
                                print(f"[face] track {tid}: {n} {s:.2f} face={int(f.bbox[2]-f.bbox[0])}px")
                                self.last_faces[tid] = [(box, n, s)]

                                if current_name == "unknown":
                                    self.identity[tid] = n
                                    self.verify_fail[tid] = 0
                                elif n == current_name:
                                    self.verify_fail[tid] = 0
                                else:
                                    # visible face disagrees with the locked identity
                                    self.verify_fail[tid] = self.verify_fail.get(tid, 0) + 1
                                    if self.verify_fail[tid] >= VERIFY_FAILS:
                                        self.identity[tid] = n   # "unknown" or someone else
                                        self.verify_fail[tid] = 0
                            elif current_name == "unknown":
                                self.last_faces[tid] = []

                    name = self.identity.get(tid, "unknown")
                    if name == "unknown" and tid not in self.moved:
                        # hasn't moved: could be a poster or statue. Grey box, never alert.
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (150, 150, 150), 1)
                        cv2.putText(frame, "static?", (x1, max(20, y1 - 8)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)
                        continue
                    if name == "unknown":
                        current_unknown.add(tid)
                        self.unknown_since.setdefault(tid, now)
                        if (now - self.unknown_since[tid] >= GRACE
                                and tid not in self.alerted):
                            self.alerted.add(tid)
                            pending.append(tid)
                    else:
                        self.unknown_since.pop(tid, None)

                    color = (0, 255, 0) if name != "unknown" else (0, 0, 255)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(frame, f"{name} #{tid}", (x1, max(20, y1 - 8)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

                    for (fx1, fy1, fx2, fy2), n, s in self.last_faces.get(tid, []):
                        cv2.rectangle(frame, (fx1, fy1), (fx2, fy2),
                                      (255, 255, 0), 1)
                        cv2.putText(frame, f"{n} {s:.2f}", (fx1, fy2 + 18),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                                    (255, 255, 0), 1)

            # FPS (smoothed)
            curr = time.time()
            dt = curr - prev_time
            prev_time = curr
            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps > 0 else (1.0 / dt)

            # overlays are drawn BEFORE recording/streaming
            cv2.putText(frame, f"FPS: {fps:.1f}", (15, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            cv2.putText(frame, f"Unknown: {len(current_unknown)}", (15, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
            for tid in pending:
                self._trigger_intrusion(tid, frame, now, len(current_unknown))
            self.recorder.add(frame)

            ok2, buf = cv2.imencode(".jpg", frame,
                                    [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok2:
                with self.lock:
                    self.latest_jpeg = buf.tobytes()
                    self.unknown_count = len(current_unknown)
                    self.fps = fps

            if self.is_file:   # don't run faster than the video's real speed
                time.sleep(max(0, frame_delay - (time.time() - now)))

        cap.release()

    def _trigger_intrusion(self, tid, frame, now, n_unknown):
        self.alerted.add(tid)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        snap = str(SNAP_DIR / f"{stamp}_id{tid}.jpg")
        clip = str(CLIP_DIR / f"{stamp}_id{tid}.mp4")
        cv2.imwrite(snap, frame)
        self.recorder.trigger(clip)
        log_event(self.camera_name, tid, "unknown", "intrusion", clip, snap)
        if now - self.last_tg > TG_COOLDOWN:
            self.last_tg = now
            send_telegram(f"INTRUSION at {self.camera_name}\n"
                          f"Unknown people in view: {n_unknown}\n"
                          f"Time: {time.strftime('%H:%M:%S')}", snap)