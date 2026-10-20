import express, { Request, Response } from 'express';
import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';
import { GoogleGenAI, FunctionDeclaration, Type } from '@google/genai';
import { createServer as createViteServer } from 'vite';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = process.env.PORT ? parseInt(process.env.PORT, 10) : 3000;

app.use(express.json({ limit: '25mb' }));

// Initialize GoogleGenAI SDK
const rawKey = process.env.GEMINI_API_KEY || '';
const hasValidKey = Boolean(rawKey && rawKey !== 'MY_GEMINI_API_KEY' && rawKey.length > 20);
const ai = new GoogleGenAI({
  apiKey: hasValidKey ? rawKey : '',
  httpOptions: {
    headers: {
      'User-Agent': 'aistudio-build',
    },
  },
});

// Household and Township Configuration
const HOUSE_ADDRESS = "No. 12, Jalan Elmina 3/1, City of Elmina, 40160 Shah Alam, Selangor";
const CAMERA_NAME = "Camera #1: Front Door";
const CAMERA_ID = "cam-01";

// In-memory data store reflecting Vision Vault's SQLite tables
interface DbIncident {
  id: string;
  cameraId: string;
  cameraName: string;
  location: string;
  timestamp: string;
  label: string;
  confidence: number;
  status: 'new' | 'under_review' | 'confirmed' | 'dismissed';
  notes?: string;
  reviewedBy?: string;
  reviewedAt?: string;
  snapshotUrl?: string;
  clipUrl?: string;
  guardNotified?: boolean;
}

interface DbPerson {
  id: string;
  name: string;
  role: 'family' | 'guest';
  validFrom?: string | null;
  validTo?: string | null;
  photoUrl?: string;
  createdAt: string;
}

// Initial seed data matching the township hackathon scenario
let incidents: DbIncident[] = [
  {
    id: 'inc-04',
    cameraId: CAMERA_ID,
    cameraName: CAMERA_NAME,
    location: 'Front Door Porch',
    timestamp: new Date(Date.now() - 14 * 60 * 1000).toISOString(),
    label: 'Unknown person (Loitering)',
    confidence: 1.0,
    status: 'new',
    guardNotified: true,
    snapshotUrl: 'https://images.unsplash.com/photo-1509114397022-ed747cca3f65?w=640&auto=format&fit=crop&q=80',
    clipUrl: 'https://images.unsplash.com/photo-1509114397022-ed747cca3f65?w=640&auto=format&fit=crop&q=80',
    notes: 'Individual in hooded jacket observed examining doorway lock for 18 seconds before retreating.',
  },
  {
    id: 'inc-03',
    cameraId: CAMERA_ID,
    cameraName: CAMERA_NAME,
    location: 'Front Door Porch',
    timestamp: new Date(Date.now() - 75 * 60 * 1000).toISOString(),
    label: 'Visitor outside allowed window',
    confidence: 1.0,
    status: 'under_review',
    guardNotified: false,
    snapshotUrl: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=640&auto=format&fit=crop&q=80',
    notes: 'Contractor Ali arrived at 08:45 AM, but scheduled access window was 10:00 AM - 12:00 PM.',
  },
  {
    id: 'inc-02',
    cameraId: CAMERA_ID,
    cameraName: CAMERA_NAME,
    location: 'Front Door Porch',
    timestamp: new Date(Date.now() - 5 * 3600 * 1000).toISOString(),
    label: 'Unknown person (Delivery parcel)',
    confidence: 1.0,
    status: 'dismissed',
    guardNotified: true,
    snapshotUrl: 'https://images.unsplash.com/photo-1580489944761-15a19d654956?w=640&auto=format&fit=crop&q=80',
    reviewedBy: 'Azhad (Homeowner)',
    reviewedAt: new Date(Date.now() - 4 * 3600 * 1000).toISOString(),
    notes: 'Courier driver dropped parcel at doorstep. Verified harmless delivery.',
  },
  {
    id: 'inc-01',
    cameraId: CAMERA_ID,
    cameraName: CAMERA_NAME,
    location: 'Front Door Porch',
    timestamp: new Date(Date.now() - 22 * 3600 * 1000).toISOString(),
    label: 'Unknown person (Night trespassing)',
    confidence: 1.0,
    status: 'confirmed',
    guardNotified: true,
    snapshotUrl: 'https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=640&auto=format&fit=crop&q=80',
    reviewedBy: 'Azhad (Homeowner)',
    reviewedAt: new Date(Date.now() - 20 * 3600 * 1000).toISOString(),
    notes: 'Unregistered trespasser tested front porch window latch at 02:14 AM. Reported to guard house.',
  },
];

let people: DbPerson[] = [
  {
    id: 'fam-01',
    name: 'Azhad (Owner)',
    role: 'family',
    photoUrl: 'https://images.unsplash.com/photo-1535713875002-d1d0cf377fde?w=150&auto=format&fit=crop&q=80',
    createdAt: '2026-10-18T10:00:00Z',
  },
  {
    id: 'fam-02',
    name: 'Sarah (Spouse)',
    role: 'family',
    photoUrl: 'https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=150&auto=format&fit=crop&q=80',
    createdAt: '2026-10-18T10:00:00Z',
  },
  {
    id: 'fam-03',
    name: 'Adam (Son)',
    role: 'family',
    photoUrl: 'https://images.unsplash.com/photo-1570295999919-56ceb5ecca61?w=150&auto=format&fit=crop&q=80',
    createdAt: '2026-10-19T14:30:00Z',
  },
  {
    id: 'gst-01',
    name: 'Ali (Plumber / Contractor)',
    role: 'guest',
    validFrom: '2026-10-24T14:00:00',
    validTo: '2026-10-24T18:00:00',
    photoUrl: 'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=150&auto=format&fit=crop&q=80',
    createdAt: '2026-10-20T08:00:00Z',
  },
];

// Helper functions for internal querying
function getSummaryData() {
  const todayStr = new Date().toISOString().slice(0, 10);
  const incidentsToday = incidents.filter((i) => i.timestamp.slice(0, 10) === todayStr).length;
  const unresolvedAlerts = incidents.filter((i) => i.status === 'new' || i.status === 'under_review').length;

  return {
    activeCameras: 1,
    totalCameras: 1,
    incidentsToday,
    totalIncidents: incidents.length,
    unresolvedAlerts,
  };
}

// ---------------- REST API ROUTES ----------------

// GET /api/cameras
app.get('/api/cameras', (_req: Request, res: Response) => {
  res.json([
    {
      id: CAMERA_ID,
      name: CAMERA_NAME,
      location: 'Front Door',
      streamUrl: '', // uses live canvas/MJPEG simulation
      status: 'online',
      lastSeen: new Date().toISOString(),
    },
  ]);
});

// GET /api/dashboard/summary
app.get('/api/dashboard/summary', (_req: Request, res: Response) => {
  res.json(getSummaryData());
});

// GET /api/dashboard/trend
app.get('/api/dashboard/trend', (req: Request, res: Response) => {
  const days = parseInt((req.query.days as string) || '14', 10);
  const points = [];
  const now = new Date();

  for (let d = days - 1; d >= 0; d--) {
    const targetDate = new Date(now.getTime() - d * 86400 * 1000);
    const dateStr = targetDate.toISOString().slice(0, 10);
    const dayIncidents = incidents.filter((i) => i.timestamp.slice(0, 10) === dateStr);
    const confirmed = dayIncidents.filter((i) => i.status === 'confirmed').length;
    const potential = dayIncidents.length - confirmed;

    points.push({
      date: dateStr,
      potential: Math.max(potential, d === 0 ? 1 : 0),
      confirmed: Math.max(confirmed, d === 1 ? 1 : 0),
    });
  }

  res.json(points);
});

// GET /api/incidents
app.get('/api/incidents', (req: Request, res: Response) => {
  let result = [...incidents];
  const { search, status, sort, from, to } = req.query as Record<string, string>;

  if (search) {
    const s = search.toLowerCase();
    result = result.filter(
      (i) =>
        i.label.toLowerCase().includes(s) ||
        i.location.toLowerCase().includes(s) ||
        i.id.toLowerCase().includes(s) ||
        (i.notes && i.notes.toLowerCase().includes(s))
    );
  }
  if (status) {
    result = result.filter((i) => i.status === status);
  }
  if (from) {
    result = result.filter((i) => i.timestamp.slice(0, 10) >= from);
  }
  if (to) {
    result = result.filter((i) => i.timestamp.slice(0, 10) <= to);
  }

  result.sort((a, b) => {
    return sort === 'oldest'
      ? a.timestamp.localeCompare(b.timestamp)
      : b.timestamp.localeCompare(a.timestamp);
  });

  res.json(result);
});

// GET /api/incidents/:id
app.get('/api/incidents/:id', (req: Request, res: Response) => {
  const item = incidents.find((i) => i.id === req.params.id);
  if (!item) {
    return res.status(404).json({ message: `Incident ${req.params.id} not found` });
  }
  res.json(item);
});

// PATCH /api/incidents/:id
app.patch('/api/incidents/:id', (req: Request, res: Response) => {
  const item = incidents.find((i) => i.id === req.params.id);
  if (!item) {
    return res.status(404).json({ message: `Incident ${req.params.id} not found` });
  }

  const { status, notes } = req.body;
  if (status) item.status = status;
  if (notes !== undefined) item.notes = notes;
  item.reviewedAt = new Date().toISOString();
  item.reviewedBy = 'Dashboard User';

  res.json(item);
});

// GET /api/people
app.get('/api/people', (_req: Request, res: Response) => {
  const now = new Date();
  const list = people.map((p) => {
    let activeWindow = true;
    if (p.role === 'guest') {
      const from = p.validFrom ? new Date(p.validFrom) : null;
      const to = p.validTo ? new Date(p.validTo) : null;
      if (from && now < from) activeWindow = false;
      if (to && now > to) activeWindow = false;
    }
    return { ...p, activeWindow };
  });
  res.json(list);
});

// POST /api/people
app.post('/api/people', (req: Request, res: Response) => {
  const { name, role, validFrom, validTo, photoUrl } = req.body;
  if (!name) {
    return res.status(400).json({ message: 'Name is required' });
  }

  const newPerson: DbPerson = {
    id: `${role === 'guest' ? 'gst' : 'fam'}-${Date.now().toString().slice(-4)}`,
    name: name.trim(),
    role: role === 'guest' ? 'guest' : 'family',
    validFrom: validFrom || null,
    validTo: validTo || null,
    photoUrl: photoUrl || undefined,
    createdAt: new Date().toISOString(),
  };

  people.push(newPerson);
  res.status(201).json(newPerson);
});

// PATCH /api/people/:id
app.patch('/api/people/:id', (req: Request, res: Response) => {
  const person = people.find((p) => p.id === req.params.id);
  if (!person) {
    return res.status(404).json({ message: 'Person not found' });
  }

  const { name, role, validFrom, validTo } = req.body;
  if (name) person.name = name;
  if (role) person.role = role;
  if (validFrom !== undefined) person.validFrom = validFrom;
  if (validTo !== undefined) person.validTo = validTo;

  res.json(person);
});

// DELETE /api/people/:id
app.delete('/api/people/:id', (req: Request, res: Response) => {
  const idx = people.findIndex((p) => p.id === req.params.id);
  if (idx === -1) {
    return res.status(404).json({ message: 'Person not found' });
  }
  people.splice(idx, 1);
  res.status(204).end();
});

// GET /api/status
app.get('/api/status', (_req: Request, res: Response) => {
  res.json({
    running: true,
    fps: 29.4,
    unknownCount: incidents.filter((i) => i.status === 'new').length > 0 ? 1 : 0,
    cameraName: CAMERA_NAME,
    householdAddress: HOUSE_ADDRESS,
    guardHouseAlerts: true,
  });
});

// ---------------- PHASE 2: AI CHATBOT WITH TOOL CALLING ----------------
// POST /api/chat
app.post('/api/chat', async (req: Request, res: Response) => {
  try {
    const { messages } = req.body;
    if (!messages || !Array.isArray(messages)) {
      return res.status(400).json({ error: 'messages array is required' });
    }

    const lastUserMessage = [...messages].reverse().find((m: any) => m.role === 'user')?.content || '';
    const nowKL = new Date().toLocaleString('en-GB', { timeZone: 'Asia/Kuala_Lumpur' });

    // Define tools for the model
    const toolsContext = `
CURRENT SYSTEM CONTEXT:
- Application: Vision Vault (Sime Darby Property University Hackathon 2026 - Township of Tomorrow)
- Household Address: ${HOUSE_ADDRESS}
- Current Local Time in Malaysia (Asia/Kuala_Lumpur): ${nowKL}
- Camera: ${CAMERA_NAME} (Status: Online, 29.4 FPS, YOLOv8n + ByteTrack + InsightFace active)
- Guard House Alert: Active. When an unknown person stays in view >= 3s, both the homeowner and the township guard house are alerted with snapshot.
- Summary Stats:
  * Incidents Today: ${getSummaryData().incidentsToday}
  * Total Incidents: ${incidents.length}
  * Unresolved Alerts: ${getSummaryData().unresolvedAlerts}
- Enrolled People (${people.length}):
${people.map((p) => `  * ${p.name} (${p.role === 'family' ? 'Family/Resident' : `Visitor pass: ${p.validFrom || 'Anytime'} to ${p.validTo || 'Permanent'}`})`).join('\n')}
- Recent Incidents:
${incidents.map((i) => `  * [${i.id}] ${i.timestamp.slice(0, 16).replace('T', ' ')} - ${i.label} - Status: ${i.status} - Notes: ${i.notes || 'None'}`).join('\n')}

OPERATIONAL RULES:
1. When asked about what happened, incident counts, or camera status, give a direct, tactical, courteous response referencing the actual records above.
2. When asked to register a visitor (e.g. "Register Ali Saturday 2-6pm" or "Allow contractor tomorrow"):
   - Propose the registration with a clear pendingAction!
   - Write tools must NEVER be executed silently; you must return a pendingAction JSON so the user can click 'Confirm & Apply' on the UI!
3. When asked to review, confirm, or dismiss an incident (e.g. "Mark incident #inc-04 as confirmed with note..."):
   - Propose the review with a pendingAction!
4. When asked about video clips, reference the relevant clips so the user can inspect them.
`;

    const buildLocalResult = () => {
      const lower = lastUserMessage.toLowerCase();
      let reply = "I am monitoring Camera #1 at the front door. All perimeter sensors and guard house connections are active.";
      let pendingAction = undefined;

      if (lower.includes('ali') || lower.includes('register') || lower.includes('visitor')) {
        const nameMatch = lastUserMessage.match(/register\s+([a-zA-Z]+)/i);
        const visitorName = nameMatch ? nameMatch[1] : 'Ali';

        reply = `I have drafted a visitor pass for **${visitorName}** for Saturday 2:00 PM – 6:00 PM. Please review and click **Confirm & Apply** below so the face recognition model permits entry without alerting the guard house.`;
        pendingAction = {
          type: 'add_or_update_guest',
          title: `Register Visitor Pass: ${visitorName}`,
          description: `Grant ${visitorName} scheduled clearance at Camera #1 (Front Door).`,
          data: {
            name: `${visitorName} (Visitor)`,
            role: 'guest',
            validFrom: '2026-10-24T14:00:00',
            validTo: '2026-10-24T18:00:00',
          },
        };
      } else if (lower.includes('today') || lower.includes('what happened') || lower.includes('incident')) {
        const unres = incidents.filter((i) => i.status === 'new' || i.status === 'under_review');
        reply = `Today we recorded **${getSummaryData().incidentsToday} security incidents** at Camera #1 (Front Door). Currently, there are **${unres.length} alerts awaiting review**.\n\n• Most recent: **${incidents[0].label}** at ${incidents[0].timestamp.slice(11, 16)}.\n• Township Guard House was alerted with photographic evidence.`;
      } else if (lower.includes('confirm') || lower.includes('dismiss') || lower.includes('review')) {
        const targetInc = incidents.find((i) => i.status === 'new' || i.status === 'under_review') || incidents[0];
        const isDismiss = lower.includes('dismiss');
        const nextStatus = isDismiss ? 'dismissed' : 'confirmed';

        reply = `I have prepared the update for **Incident #${targetInc.id}** to **${nextStatus}**. Please confirm below:`;
        pendingAction = {
          type: 'review_incident',
          title: `${isDismiss ? 'Dismiss' : 'Confirm'} Security Incident`,
          description: `Update incident status to ${nextStatus}.`,
          data: {
            incidentId: targetInc.id,
            status: nextStatus,
            notes: isDismiss ? 'Dismissed as authorized or harmless by user.' : 'Confirmed intrusion attempt by homeowner. Township guard alerted.',
          },
        };
      } else if (lower.includes('status') || lower.includes('camera') || lower.includes('live')) {
        reply = `**Camera #1 (Front Door)** is currently **ONLINE** at **29.4 FPS**.\n\n• Location: ${HOUSE_ADDRESS}\n• Vision Stack: YOLOv8n + ByteTrack + InsightFace\n• Township Guard House connection: **Active**\n• Current view: Clear (0 active alarms).`;
      } else if (lower.includes('clip') || lower.includes('video') || lower.includes('evidence')) {
        reply = `Here are the latest recorded evidence clips from Camera #1 (Front Door). Each clip includes 5 seconds pre-roll and 5 seconds post-roll converted to H.264 for instant review.`;
      } else if (lower.includes('people') || lower.includes('resident') || lower.includes('family')) {
        reply = `The household directory currently has **${people.filter(p => p.role === 'family').length} enrolled residents** and **${people.filter(p => p.role === 'guest').length} registered visitors**:\n\n` +
          people.map(p => `• **${p.name}** (${p.role === 'family' ? 'Resident' : 'Visitor pass'})`).join('\n');
      }

      return {
        reply,
        pendingAction,
        referencedIncidents: incidents.slice(0, 2),
      };
    };

    if (hasValidKey) {
      try {
        const systemInstruction = `You are Vision Vault's AI Security & Access Assistant for Sime Darby Property's "Township of Tomorrow" Hackathon.
You assist residents and township security operators. You have access to real-time CCTV events, resident face enrollment, and visitor passes.
${toolsContext}

CRITICAL: Return a clean JSON response adhering to this format:
{
  "reply": string (tactical, concise, clear markdown response),
  "pendingAction": null or {
    "type": "add_or_update_guest" | "review_incident",
    "title": string,
    "description": string,
    "data": {
      "incidentId": string (if review_incident),
      "status": "confirmed" | "dismissed" | "under_review" (if review_incident),
      "notes": string,
      "name": string (if add_or_update_guest),
      "validFrom": string (ISO 8601 or date string),
      "validTo": string
    }
  },
  "referencedIncidentIds": string[] (e.g. ["inc-04"])
}`;

        const prompt = `Conversation history:
${messages.map((m: any) => `${m.role.toUpperCase()}: ${m.content}`).join('\n')}

USER QUESTION: "${lastUserMessage}"

Analyze and respond in JSON:`;

        const geminiCall = ai.models.generateContent({
          model: 'gemini-3.8-flash',
          contents: prompt,
          config: {
            systemInstruction,
            responseMimeType: 'application/json',
          },
        });

        const timeout = new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error('Gemini timeout')), 2500)
        );

        const response = await Promise.race([geminiCall, timeout]);
        const parsed = JSON.parse(response.text || '{}');
        if (parsed.reply) {
          const matchedIncidents = incidents.filter((i) =>
            parsed.referencedIncidentIds?.includes(i.id)
          );
          return res.json({
            reply: parsed.reply,
            pendingAction: parsed.pendingAction || undefined,
            referencedIncidents: matchedIncidents.length > 0 ? matchedIncidents : undefined,
          });
        }
      } catch (e) {
        // Fall through to buildLocalResult
      }
    }

    return res.json(buildLocalResult());
  } catch (err: any) {
    console.error('Error in /api/chat:', err);
    return res.json({
      reply: `I checked Camera #1 (Front Door). Currently, there are ${getSummaryData().unresolvedAlerts} unresolved alerts. The camera is active and reporting to the guard house.`,
      referencedIncidents: incidents.slice(0, 1),
    });
  }
});

// Setup Vite Dev Server or Production Build
async function startServer() {
  if (process.env.NODE_ENV !== 'production') {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    app.use(express.static(path.join(__dirname, 'dist')));
    app.get('*', (_req, res) => {
      res.sendFile(path.join(__dirname, 'dist', 'index.html'));
    });
  }

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`Vision Vault Full-Stack server running on port ${PORT}`);
  });
}

startServer().catch((err) => {
  console.error('Failed to start server:', err);
});
