# Vision Vault

AI-powered smart township home security: a CCTV/webcam feed is watched by YOLOv8 + InsightFace;
unknown people trigger a recorded incident, a snapshot, and Telegram alerts to the homeowner and guard house.

**Hackathon:** Sime Darby Property University Hackathon 2026 — theme "Township of Tomorrow".
**Deadline: Friday 23 Oct 2026.** Owner / lead: Azhad (backend + vision). Frontend was built by a teammate.

## Positioning — "Township of Tomorrow" (decided Tue 20 Oct)
**Vision Vault is smart home security that plugs into the township.** Each home runs its own camera + AI.
Recognised residents pass silently; registered **visitors** are allowed inside their time window (like a gated
community's visitor registration); an unknown person alerts **both the homeowner and the township guard house**
with a snapshot and clip. Footage and face data stay inside the home (privacy by design).

- UI wording: "residents" / "family", **"visitors"** (never "guests"), "guard house". No "theft" wording.
  The word "township" is fine where it means the community. Backend role names stay `family` / `guest`.
- Scope stays one home, one camera. A township-wide guard dashboard (many homes) is KIV — pitch it, don't build it.

> This file is the single source of truth for status and plan. It is read by Antigravity (`AGENTS.md`)
> and by Claude Code (via `CLAUDE.md` → `@AGENTS.md`). **When you finish a task, tick it here.**

## Ground rules for AI agents
- **The backend is authoritative.** The frontend adapts to the backend's model, not the other way round.
- **Do not delete anything** in "Pending deletion" until the owner confirms in chat.
- **Login/auth is KIV.** Do not build it. Keep `VITE_AUTH_ENABLED=false`; leave `AuthContext` in place.
- Single household, single camera (`cam-01`). Do not build multi-camera management.
- Keep changes small and demo-focused; nothing new after Thursday night except bug fixes.

## Architecture

```
 Webcam / test video  (VIDEO_SOURCE env: "0" = webcam, or a file path)
        │
        ▼
 VisionEngine thread (engine.py)
   YOLOv8n + ByteTrack → person tracks
   InsightFace on head crop → matched identity or "unknown"
   Rules: track ≥ 10 frames, must have moved, unknown for 3 s → intrusion
        │ annotated JPEG                       │ intrusion
        ▼                                      ▼
   /video (MJPEG stream)           vault_utils.py: snapshot, clip (5 s pre + 5 s post,
        │                          re-encoded to H.264), SQLite `events`, Telegram (15 s cooldown)
        ▼                                      │
 FastAPI  main.py + api.py  (/api/*)  ◄────────┘
        │  REST + WebSocket
        ▼
 frontend/  React 18 + TypeScript + Vite + Tailwind  (live mode, no login)
```

Boxes/labels are drawn into the video frame by the engine; the frontend does not draw live overlays.

## Tech stack
- **Backend:** Python, FastAPI, Ultralytics YOLOv8n, InsightFace (`buffalo_s`, CPU), OpenCV, SQLite, imageio-ffmpeg
- **Frontend:** `frontend/` — React 18, TypeScript, Vite 5, Tailwind 3, React Router 6, Recharts, lucide-react
- **Alerts:** Telegram bot (`TG_TOKEN`, `TG_CHAT_ID` in `.env`)
- **Hardware:** USB webcam

## Key files
| Path | Purpose |
|---|---|
| `engine.py` | `VisionEngine`: capture loop, detection, tracking, face recognition, intrusion rules |
| `vault_utils.py` | DB schema/migrations, `log_event`, `ClipRecorder`, H.264 conversion, Telegram |
| `api.py` | `/api/*` router used by `frontend/` (maps `events` rows → frontend `Incident` shape) |
| `main.py` | App setup, CORS, `/video` MJPEG stream, static `/clips` and `/snapshots` |
| `faces/<name>/` | Enrollment photos (one folder per person). `faces/embeddings.pkl` = cache |
| `frontend/src/types/index.ts` | Frontend data types = API contract |
| `frontend/src/services/` | The only place the frontend fetches data |
| `frontend/.env.local` | Frontend settings (live mode, websocket, auth off) |
| `.env` | `TG_TOKEN`, `TG_CHAT_ID`, optional `VIDEO_SOURCE` (later: `ANTHROPIC_API_KEY`) |

## Setup & run
```bash
pip install ultralytics insightface fastapi uvicorn python-dotenv opencv-python requests imageio-ffmpeg
```
- Backend (from `vision_vault/`): `py -m uvicorn main:app --port 8000`
  - On Azhad's laptop use `py` (Python 3.14 has the packages); `python` there is only the Microsoft Store stub.
  - Test video instead of webcam: set `VIDEO_SOURCE=test_videos/test1.mp4` in `.env`
- Frontend (from `vision_vault/frontend/`): `npm install` once, then `npm run dev` → http://localhost:5173
- Node.js 18+ required.

## Current API (what actually exists)
| Method | Path | Notes |
|---|---|---|
| GET | `/video` | MJPEG stream (use `<img>`, not `<video>`) |
| GET | `/clips/*`, `/snapshots/*` | Static evidence files |
| GET | `/api/cameras` | Single camera `cam-01`; POST/PUT/DELETE return 501 |
| GET | `/api/incidents` | `?search&cameraId&status&from&to&sort` |
| GET / PATCH | `/api/incidents/{id}` | PATCH `{status, notes}`; status ∈ new, under_review, confirmed, dismissed |
| GET | `/api/dashboard/summary`, `/api/dashboard/trend?days=14` | |
| WS | `/api/ws/events` | `{type:"alert", data: Incident}` and `{type:"camera_status", ...}` |
| GET | `/status`, `/events` | **Legacy**, only used by old `dashboard/` — to be removed |

**`events` table:** `id, ts (epoch), camera, track_id, identity, event_type, clip_path, snapshot_path, status, notes, reviewed_at`.

## Status (as of Tue 20 Oct 2026)

### Done
- [x] Person detection + ByteTrack tracking, face recognition against `faces/`
- [x] Intrusion rules (min track age, must move, 3 s grace), static-figure suppression
- [x] Snapshot + pre/post-roll clip recording, H.264 conversion for browser playback
- [x] SQLite event log with review status/notes
- [x] Telegram alerts with photo
- [x] Teammate's frontend wired to backend in live mode (incidents, dashboard, live feed, WebSocket alerts)

### Not done (previously mis-reported as done)
- [ ] **Guest whitelisting** — no code exists. Everyone in `faces/` is treated as permanently allowed.

### Known issues
- `faces/embeddings.pkl` is only rebuilt when missing → new photos are ignored until it is deleted.
- Alert WebSocket sends `clipUrl` ~10 s before the clip is finished/converted → clip fails if opened immediately.
- Incident `confidence` is hard-coded `1.0` (alerts are rule-based).
- Engine per-track dicts (`identity`, `track_age`, `first_pos`, …) grow forever on long runs.
- Frontend still branded "Township CCTV / theft detection" with roles (camera management already removed).
- Project sits in OneDrive — push to GitHub after every feature.
- **Security limitation (state it in the demo):** a printed photo of a family member is accepted (no liveness check).

## Plan to Friday

### Phase 0 — Clean-up & frontend adaptation (Tue 20)
- [x] `git init`, root `.gitignore` adds `node_modules/`, `dist/`, `.env.local`, `*.pt`; first commit; pushed to https://github.com/Arcane2798/vision_vault
- [x] Owner reviews "Pending deletion" list, then delete confirmed items
- [ ] Backend: add `GET /api/status` (`running, fps, unknownCount, cameraName`); remove legacy `/status` and `/events` from `main.py`
- [ ] Frontend rebrand: `VITE_APP_NAME=Vision Vault` (in `.env.local` and the `env.ts` fallback); sidebar subtitle
      "Smart township home security"; `index.html` title; remove all "theft" wording and "Township CCTV" names
      (`grep -rniE "township|theft" frontend/src frontend/index.html`); follow the Positioning wording rules
- [x] Frontend: remove Cameras page from nav + routes (single fixed camera)
- [ ] Frontend: event-type labels in `utils/format.ts` — `intrusion` → "Unknown person", `guest_outside_window` → "Visitor outside allowed time"
- [ ] Frontend: incident detail shows identity; hide confidence for rule-based events
- [ ] Frontend: dashboard / live page shows FPS + "unknown in view" from `/api/status`
- [x] Rewrite root `README.md` to match reality; replace `frontend/README.md` (and `.env.example` now holds the live Vision Vault settings)
- [x] README: "Why it matters for the Township of Tomorrow" section

### Phase 1 — People & guest whitelisting (Wed 21) — core feature
- [ ] **Embedding cache fix:** cache per person with a signature (file names + mtimes); recompute only changed folders
- [ ] **`people` table** in `vault.db`: `id, name (= faces/ folder), role ('family'|'guest'), valid_from, valid_to (epoch, NULL for family), created_at`.
      Folders in `faces/` with no row are treated as `family` (back-compat).
- [ ] **Engine:** `reload_people()` (thread-safe, under `self.lock`) called by the API after changes. After a face match:
      family → allowed (green); guest inside window → allowed (cyan, label `guest:<name>`);
      guest outside window → event `guest_outside_window` + Telegram (orange); no match → `intrusion` (red)
- [ ] **API** (needs `pip install python-multipart`):
      `GET /api/people`, `POST /api/people` (multipart: name, role, validFrom?, validTo?, photos[]),
      `PATCH /api/people/{id}` (role / window), `DELETE /api/people/{id}` (row + folder),
      `POST /api/people/{id}/photos`, `GET /api/people/{id}/photo` (thumbnail; `faces/` is never mounted publicly)
- [ ] **Frontend `/people` page** ("Residents & Visitors"): list family + visitors (thumbnail, role, window, active badge),
      add person with photo upload, edit visitor window, delete with confirm. Types in `types/index.ts`, calls in `services/index.ts`.

### Phase 2 — Chatbot with tool calling + guard-house alert (Thu 22)
- [ ] `POST /api/chat` `{messages}` → `{reply, pendingAction?}` using the `anthropic` Python SDK, model `claude-sonnet-5-5`,
      key `ANTHROPIC_API_KEY` in `.env`. System prompt includes current local time (Asia/Kuala_Lumpur).
- [ ] Tools are plain Python functions reusing existing queries:
      read — `get_summary`, `list_incidents(from, to, status, limit)`, `get_incident(id)`, `get_live_status`, `list_people`;
      write — `review_incident(id, status, notes)`, `add_or_update_guest(name, valid_from, valid_to)`.
- [ ] Write tools are **never executed by the model**: they return `pendingAction`; the frontend shows a Confirm button,
      and on confirm calls the normal REST endpoint itself.
- [ ] Frontend chat panel (slide-over from the top bar), works from every page.
- [ ] **Guard-house alert** (small, ~1 h): `.env` gets `GUARD_TG_CHAT_ID` and `HOUSE_UNIT` (e.g. "No. 12, Jalan Elmina 3/1").
      On `intrusion` only, send a second Telegram message with the unit address + snapshot to the guard-house chat
      (same cooldown; skip silently if unset). Add `guard_notified` column; incident detail shows "Guard house notified".
      `guest_outside_window` goes to the homeowner only.
- [ ] Fix clip race: add `clip_ready` column, set to 1 when H.264 conversion finishes; API exposes `clipReady`;
      frontend shows "Clip processing…" until ready.

### Phase 3 — Polish & demo (Fri 23, morning only)
- [ ] Prune engine per-track state for tracks unseen > 30 s
- [ ] Optional quick win: weapon flag using YOLOv8n's existing COCO classes (`knife`, `scissors`, `baseball bat`)
- [ ] Demo rehearsal with `test_videos/test1.mp4` and live webcam; tidy sample data in `vault.db`
- [ ] Final README, final commit, tag `v1.0-demo`

**Demo script:** resident walks in (green, no alert) → visitor inside their window (cyan) → same visitor after the window
(orange, homeowner alerted) → stranger (red: homeowner **and guard house** get Telegram photos, incident appears live)
→ open incident, play clip, mark confirmed → ask chatbot "what happened at the door today?" and
"register Ali as a visitor Saturday 2–6pm" (confirm). Close with the township-wide vision slide.

## KIV (after Friday)
Login & roles · liveness / anti-spoofing · dedicated weapon model (guns) · ESP32 buzzer · township-wide guard dashboard
(many homes) · path/trajectory trails · multi-camera · remove frontend mock mode (`src/mocks/`).

## Pending deletion (awaiting owner check — do not delete yet)
| Item | Why |
|---|---|
| Old files in `clips/`, `snapshots/` + their `vault.db` rows | Owner chose to keep for now; may trim before the demo |

Deleted on Tue 20 Oct (owner-approved): `dashboard/`, `Backup/`, `frontend/dist/`, `frontend/.env.local.mock.bak`,
`__pycache__/`, `faces/rdj/`, `faces/embeddings.pkl` (rebuilds on next start), `CamerasPage.tsx`, `CameraFormModal.tsx`
(route, sidebar link and dashboard link removed; typecheck passes).

## Conventions
- **Python:** 4-space indentation (as in existing code), type hints where helpful, small functions, no new frameworks
- **Frontend:** TypeScript, functional components + hooks, Tailwind classes. Pages compose, components display,
  services fetch — pages never call `fetch` directly. New API types go in `src/types/index.ts`.
- **Git:** one commit per feature, descriptive messages; tick the checklist above in the same commit.
