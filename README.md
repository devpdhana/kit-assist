# KIT Assist: a multi-agent voice assistant for Knotopian Institute of Technology

[![CI](https://github.com/devpdhana/kit-assist/actions/workflows/ci.yml/badge.svg)](https://github.com/devpdhana/kit-assist/actions/workflows/ci.yml)

A real-time **voice** assistant for a dummy college. Prospective students, students and teachers talk to it and hear spoken replies. A **LangGraph supervisor** orchestrates specialist agents: it understands the query, calls the college system, checks the rulebook, evaluates every reply before it is spoken, and **asks before acting**.

**Stack:** React (Vite) · Python FastAPI · LangChain + LangGraph · Groq (Whisper STT + LLMs) · Google Gemini (fallback) · SQLite

## Architecture: supervisor pattern

```
 React (voice UI) ──audio──▶ FastAPI /api/voice ──▶ STT (Groq Whisper ▸ Gemini)
                                     │ text
                         ┌───────────▼───────────┐
          ┌─────────────▶│      SUPERVISOR       │◀──────────────┐
          │              │ 1 LLM call: intent +  │               │  every agent reports
          │              │ self-contained task;  │               │  back to the supervisor
          │              │ deterministic guards  │               │
          │              └──┬──────┬──────┬───┬──┘               │
          │                 │      │      │   │                  │
   ┌──────┴──────┐  ┌───────▼──┐ ┌─▼──────┴┐ ┌▼──────────────┐ ┌─┴─────────────────┐
   │  SERVICE    │  │KNOWLEDGE │ │RESPONSE │ │  EVALUATION   │ │ HUMAN CONFIRMATION│
   │ KB = college│  │ PDFs:    │ │ spoken  │ │ rules+metrics │ │ LangGraph         │
   │ API catalog │  │ handbook,│ │ reply   │ │ (LLM judge    │ │ interrupt() until │
   │ + Policy    │  │ circulars│ │         │ │ if borderline)│ │ the user says yes │
   │ Guard       │  │ brochure │ │         │ │               │ │                   │
   └─────────────┘  └──────────┘ └─────────┘ └───────────────┘ └───────────────────┘
```

**One turn, for example "Can I take leave on Monday for my cousin's wedding?"**

1. **Supervisor** works out the context (intent = `action_request`, task = "Apply personal leave on 2026-11-23…") using the *college's* date and the conversation history. Then it routes to **Service**.
2. **Service agent** plans the call from its API knowledge base (`submit_leave_request`) and runs the **Policy Guard**, which finds the DBMS Internal Assessment that day, so the request is **blocked (Rule 6.1.4)**. It reports to the supervisor.
3. **Supervisor** checks the result. A retryable error (timeout or 5xx) is **retried up to 2 times**; a blocked or invalid request is not. It then routes to **Response**.
4. **Response agent** writes a short spoken reply from the evidence only.
5. **Evaluation agent** scores the reply on grounding (every number must appear in the evidence), honesty (no "submitted" unless it was executed), the confirmation question, privacy, error honesty and voice fit. If it fails, the supervisor sends it back for revision (max 2). A borderline score triggers an LLM judge.
6. **Supervisor** finishes and the reply is spoken. For a write that passed the Policy Guard, the graph **pauses** (`interrupt`) at **Human Confirmation**. The user's next "yes" resumes the same thread, the Service agent executes with an `Idempotency-Key`, and the result is spoken.

Every turn's **agent trace** (agent, action, timing, evaluation score) is shown in the UI and stored in history.

## What makes it reliable

| | |
|---|---|
| 🛡️ **Policy Guard** | Simulates leave and on-duty requests against the handbook using the student's real timetable, calendar and attendance: exam-day bans, 24h notice, no back-dated leave, the 10-day OD cap, overlaps, holidays, working Saturdays, and the attendance-impact maths of Rule 4.2.3 (*"CS304 would drop from 76% to 73.1%"*). |
| 📚 **Document trust engine** | Live-syncs documents every 5 minutes, so new circulars appear automatically. A circular in force overrides the handbook. The 2025 edition is superseded. Drafts are non-binding, and the planted "fees are waived" AI instruction in Annexure E is stripped. Answers cite rule numbers. |
| ✅ **Human-in-the-loop** | Writes are staged and executed only after an explicit "yes" (LangGraph `interrupt` / `Command(resume=…)`). The rule that a staged action must not be described as done is enforced by the Evaluation agent. |
| 🔁 **Failover everywhere** | LLM: 3 Groq models (rotated to spread free-tier quotas), then Gemini, then rule-based agents. STT: Groq Whisper, then Gemini. College API: timeouts, retries and stale-cache fallback. |
| 🔒 **Role isolation** | The Service agent's API catalog is filtered by role server-side. Visitors can't reach student data whatever the LLM says. The college token never leaves the server. |

## Project layout

```
backend/                       Python 3.12 · FastAPI · LangGraph
  src/agents/                  supervisor, service, knowledge, response, evaluation
  src/prompts/*.md             agent system prompts
  src/dtos/                    Pydantic models (QueryContext, ServicePlan, ResponseEvaluation…)
  src/kb/api_catalog.py        Service agent's knowledge base: every college API, role, params
  src/workflows/               LangGraph: state/, nodes/, routing/, assistant_workflow.py
  src/services/                college client, policy guard, knowledge base, LLM factory, STT, sessions, history
  src/api/routes.py            REST API (chat, voice, auth, history, health)
  tests/                       pytest (39): policy, knowledge trust, routing, evaluation, role scoping, history
frontend/                      React + Vite
  src/components/              VoiceAssistant, AgentTrace
  src/hooks/                   useVoiceRecorder (MediaRecorder + VAD), useSpeech (TTS)
  src/pages/                   Home (public), Login, Assistant, History
```

## Run locally

```bash
# backend
cd backend
cp .env.example .env          # COLLEGE_API_KEY, GROQ_API_KEY, GEMINI_API_KEY, SESSION_SECRET
uv sync
uv run uvicorn src.main:app --port 8000
uv run pytest                 # 39 tests
uvx ruff check src tests      # lint (clean)

# frontend (dev, proxies /api to :8000)
cd frontend && npm install && npm run dev      # http://localhost:5173
# or build once and let FastAPI serve it at http://localhost:8000
npm run build
```

Free AI keys are available from Groq (https://console.groq.com/keys) and Gemini (https://aistudio.google.com/apikey). Demo logins (password `Kit@2026`): students `arjun`, `priya`, `kavya`…; teachers `lakshmi`, `senthil`.

## Deploy

| Part | Where | Why |
|---|---|---|
| **Backend** (FastAPI + LangGraph) | Coolify, Docker image from `backend/Dockerfile`, volume at `/app/data` for history | Always on: no cold starts for a voice app |
| **Frontend** (React build) | Render Static Site (`render.yaml`); `frontend/vercel.json` is an equivalent fallback | Free static hosting that never sleeps |

The frontend host **rewrites `/api/*` to the backend**, so the browser talks to a single origin. Secure SameSite=Strict session cookies and the CSRF header keep working without CORS or third-party cookies.
The backend needs `COLLEGE_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY` and `SESSION_SECRET` as environment variables. The health check is at `/api/health`, and API docs are at `/api/docs`.
The root `Dockerfile` builds an all-in-one image (backend serving the built frontend) for single-host deployments.

## Known limitations

- **Single instance by design.** Sessions and paused confirmations (LangGraph `MemorySaver`) live in process memory, and history is stored in SQLite on a persistent volume. A backend restart logs users out and drops any pending "Shall I go ahead?". Scaling out would mean Redis-backed sessions and a Postgres checkpointer.
- **Keyword document search (BM25)**, not embeddings. It's fast and free, but unusual phrasings can miss. The Knowledge agent blends a filtered search with the best overall hits to compensate.
- **Voice output uses the browser's speech synthesis**, so voice quality depends on the device.
- **No frontend unit tests.** The frontend is covered by lint, a build check in CI, and manual end-to-end testing.

## Security

Secrets come from environment variables only. Session cookies are HMAC-signed, httpOnly and SameSite=Strict, and the session rotates on login. Every write needs a CSRF custom header. Endpoints are rate-limited and request sizes are capped. Responses carry CSP, HSTS, nosniff and frame-deny headers. History is filtered by owner, and LangGraph checkpoint types use an explicit allow-list (no pickle). Document text is treated as data, never instructions.
