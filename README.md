# Personal AI Secretary at Work

## 1) Project Overview
This repository is an internal AI assistant platform for workplace operations. It provides:
- AI chat for employee requests (leave, expenses, travel, IT tickets, access, workspace booking)
- Document upload and policy Q&A over indexed company documents
- Approval workflows and request tracking UI
- Calendar event sync (optional) via Google Calendar

The system is split into a Next.js frontend (`frontend/web`) and a FastAPI backend (`services/core-ai`) with supporting infrastructure in Docker (`infra/docker`).

## 2) UI Snapshot
These screenshots from `other_files/` spotlight the live Next.js experience powered by the backend.

**Chat example**  
![Sick leave chat](other_files/sick-leave-chat.png)
This example shows a sick-leave request.

![Room booking chat](other_files/reserve-room-chat.png)
This example shows a room-booking request.

![Car booking chat](other_files/reserve-car-chat.png)
This example shows a car-booking request.

**Document upload**  
![Document upload](other_files/upload-file-page.png)  
The interface for uploading documents to vector database.

**Request tracking**  
![Request board](other_files/my-request-board.png)  
“My Requests” aggregates leave, expense, travel, access, tickets, and workspace bookings.

**Approvals view**  
![Approvals](other_files/leave-request-approval-page.png)  
![Approvals](other_files/expense-travel-request-approval-page.png)  
![Approvals](other_files/access-request-approval-IT-ticket-page.png)  
Each approver card surfaces the metadata that the backend domain routes capture (`/domain/requests`, `/domain/expenses`, `/domain/access-requests`). Approval buttons hit the same `approve`/`reject` endpoints used by the agent when finalizing requests.

## 3) How the System Works

![system-workflow](other_files/chatbot-workflow.png)  

1. User sends a message from the chat UI.
2. Backend runs a LangGraph pipeline: `Router -> Domain -> Guardrail`.
3. Router classifies intent (`request`, `doc_qa`, `generic`) and request domain (`hr`, `ops`, `it`, `workspace`).
4. Domain agent extracts fields, asks clarifying questions if required, and calls internal domain APIs.
5. Guardrail enforces sensitivity constraints (for example salary-related access).
6. Results are returned as final response + actions + events; UI streams updates via WebSocket.

### 3.1 Agent Lifecycle (Detailed)
1. Frontend sends message to:
   - WebSocket: `/api/v1/chat/stream` (primary path, streaming UX)
   - HTTP fallback: `/api/v1/chat`
2. Backend creates/loads `session_id` and reads any `pending_request` from Redis.
3. `ChatState` is built with:
   - user message
   - user identity/roles
   - tenant id
   - pending request context
4. LangGraph executes nodes in strict order:
   - `router_node`
   - `domain_node`
   - `guardrail_node`
5. Final result is persisted:
   - Redis: conversation + pending request state
   - MongoDB: session title + user/assistant messages + events/actions
6. Response is streamed:
   - intermediate events (`activity`, routing/tool statuses)
   - token deltas
   - `final_response` object (message, actions, pending_request)

### 3.2 Router Agent (Intent + Domain + Sub-route)
Router has multi-stage classification:
- Stage 1 (`main_route`): `request`, `doc_qa`, or `generic`
- Stage 1 sensitivity: `normal`, `hr_personal`, `salary`, `access`
- Stage 2 (`request_domain`) for request flows: `workspace`, `hr`, `ops`, `it`
- Stage 3 (`sub_route`) request type:
  - HR: `leave`
  - OPS: `expense` or `travel`
  - IT: `access` or `ticket`
  - Workspace: `workspace_booking`

Important behavior:
- If there is an unfinished `pending_request`, router resumes it directly.
- For `doc_qa`, router also classifies document scope (`policy_hr`, `policy_it`, `policy_travel_expense`).
- If LLM classification fails/invalid, router falls back to safe defaults (`generic`) and heuristic logic.

### 3.3 Domain Agent (Execution + Clarification)
Domain agent is where business work happens:
- Chooses handler by domain: HR, OPS, IT, Workspace, or Doc-QA.
- Extracts structured fields using:
  - LLM JSON extraction prompts
  - deterministic regex/date/time heuristics
  - normalization rules for amounts, roles, dates, resource names
- Builds a `pending_request` if required fields are missing.
- Asks next clarification question(s) and waits for user reply.
- On complete data, submits actions via internal tool runner.

Examples of required fields:
- Leave: `leave_type`, `start_date`, `end_date`
- Expense: `amount`, `currency`, `date`, `category`
- Travel: `origin`, `destination`, `departure_date`, `return_date`
- Access: `resource`, `requested_role`, `justification`, `needed_by_date`
- Ticket: `subtype`, `description`, `location`, `entity`, `incident_date`
- Workspace booking: `resource_type`, `resource_name`, `start_time`, `end_time`

### 3.4 Tool Runner and Domain APIs
When `TOOLS_ENABLED=true`, the domain agent calls internal endpoints through `ToolRunner`.
- Base URL is `DOMAIN_SERVICE_URL` (default points back to `core-ai` domain routes).
- For document search calls, it uses `CORE_API_URL`.
- Tool runner supports `GET/POST/PATCH/DELETE`.
- Optional `SERVICE_AUTH_TOKEN` is attached as bearer auth between services.

Primary internal routes called by the agent are under:
- `/api/v1/domain/*` (leave/expense/travel/access/tickets/workspace)
- `/api/v1/documents/*` (upload/search)

### 3.5 Guardrail Agent
Guardrail runs after domain processing and can override output.
- Current enforced rule:
  - if sensitivity is `salary` and user lacks `hr_approver` or `system_admin`, response is blocked.
- When blocked, assistant returns a safe response and clears action side effects in output.

### 3.6 Memory, Sessions, and Titles
- Redis stores short-lived conversational state and pending request continuation.
- MongoDB stores durable chat sessions and message history for the UI.
- Session titles are auto-generated using LLM summary with rule-based fallback.
- Multi-tenant routing is controlled via `X-Tenant-Id` / `tenant_id`.

### 3.7 Events Sent to Frontend
The backend emits structured events so UI can show real-time progress:
- `agent_started`, `agent_finished`
- `activity` (human-readable stage updates)
- `router_*` classification events
- `tool_call`, `tool_result`, `tool_error`
- `token_delta`
- `final_response`

This is why the chat page can show “latest agent activity”, streaming assistant text, and pending field chips in real time.

Request and data flow:
- Structured domain data: PostgreSQL
- Chat session memory: Redis
- Chat history and session metadata: MongoDB
- Document embeddings and retrieval: Qdrant (+ local fallback)
- Document files: MinIO (S3-compatible)
- LLM endpoint: Ollama (`qwen3:0.6b` by default)
- Calendar write-back: Google Calendar API

## 4) Tech Stack (Frontend + Backend)

![Architecture](other_files/system-architecture.png)

### Frontend
- Next.js 15 (App Router)
- React 18 + TypeScript
- Tailwind CSS
- Framer Motion, Zustand, SWR
- Jest + Testing Library

### Backend
- FastAPI + Uvicorn
- LangGraph + LangSmith tracing hooks
- SQLModel + SQLAlchemy + PostgreSQL
- Redis, MongoDB
- Qdrant + Sentence Transformers + LlamaIndex
- MinIO (boto3)
- OCR/PDF extraction: Tesseract, PyPDF2, PyMuPDF
- Auth: local dev identity (no external IdP required)

### Infrastructure / Observability
- Docker Compose
- Prometheus + Grafana

## 5) Environment Configuration and System Preparation

### 5.1 Prerequisites
- Docker Desktop + Docker Compose
- At least 8GB RAM recommended (more is better for embeddings/LLM)
- If using GPU for Ollama, NVIDIA runtime support
- Optional for local non-Docker backend: Python 3.11+, Node.js 20+

### 5.2 Configure Docker Environment
1. Copy env template:
   - `copy infra/docker/.env.example infra/docker/.env`
2. Create external Ollama volume (required by compose):
   - `docker volume create ollama_data`
3. Review `infra/docker/.env` and adjust values.

Important variables in `infra/docker/.env`:
- Core backend:
  - `AUTH_DISABLED` (default `true`)
  - `TOOLS_ENABLED` (set `true` to allow chat to call internal domain APIs)
  - `LLM_BASE_URL`, `LLM_CHAT_PATH`, `LLM_MODEL`
- Database/storage/search:
  - `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
  - `QDRANT_HOST`, `QDRANT_PORT`, `QDRANT_*`
  - `STORAGE_ENDPOINT`, `STORAGE_BUCKET`, `STORAGE_ACCESS_KEY`, `STORAGE_SECRET_KEY`
- Optional tracing:
  - `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`, `LANGCHAIN_ENDPOINT`

### 5.3 Google Calendar Setup
1. Create a Google Cloud service account and enable Google Calendar API.
2. Download the service-account JSON.
3. Place it at:
   - `infra/docker/secrets/google_calendar_credentials.json`
4. In `infra/docker/.env`, set:
   - `GOOGLE_CALENDAR_ENABLED=true`
   - `GOOGLE_CALENDAR_CREDENTIALS=/run/secrets/google_calendar_credentials.json`
   - `GOOGLE_CALENDAR_ID=primary` (or a specific calendar ID)
   - `GOOGLE_CALENDAR_TIMEZONE=Asia/Bangkok` (or your timezone)
   - `GOOGLE_CALENDAR_SUBJECT=` only if using Workspace domain-wide delegation

### 5.4 Frontend Runtime Variables
The frontend uses these env vars (already set in compose):
- `NEXT_PUBLIC_API_BASE` (default `http://localhost:8000/api/v1`)
- `NEXT_PUBLIC_WS_BASE` (default websocket base)
- `NEXT_PUBLIC_APPROVALS_WS` (optional approvals websocket)
- `NEXT_PUBLIC_TENANT_ID` (default `default`)
- `NEXT_PUBLIC_DEMO_USER` (default `demo-user`)

### 5.5 Notes Before Running
- `infra/docker/.env` currently exists in this repo; treat it as sensitive and avoid committing real secrets.
- First startup may take time because Ollama pulls model `qwen3:0.6b` and embedding models may be downloaded.
- If your machine has no GPU and Ollama fails to start, remove `gpus: all` from the `llm` service in `infra/docker/docker-compose.yml`.

## 6) Step-by-Step: Run the System

## Option A: Docker Compose (Recommended)
1. From repo root, prepare env and volume:
   - `copy infra/docker/.env.example infra/docker/.env`
   - `docker volume create ollama_data`
2. (Optional) Add Google credentials file to `infra/docker/secrets/`.
3. Start all services:
   - `docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up --build`
4. Open apps:
   - Frontend: `http://localhost:3000`
   - Backend health: `http://localhost:8000/api/v1/health`
   - Prometheus: `http://localhost:9090`
   - Grafana: `http://localhost:3001`
5. Stop services:
   - `docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env down`

## Option B: Local Development (Backend + Frontend)
1. Start dependencies only via Docker (Postgres, Redis, MongoDB, Qdrant, MinIO, Ollama):
   - `docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up -d postgres redis mongodb qdrant minio llm`
2. Backend setup:
   - `cd services/core-ai`
   - `python -m venv .venv`
   - `.\.venv\Scripts\activate`
   - `pip install -r requirements.txt`
3. Export backend env vars (PowerShell example):
   - `$env:DATABASE_URL='postgresql://ai:ai_password@localhost:5432/ai_secretary'`
   - `$env:REDIS_URL='redis://localhost:6379/0'`
   - `$env:MONGO_URL='mongodb://localhost:27017'`
   - `$env:QDRANT_HOST='localhost'`
   - `$env:STORAGE_ENDPOINT='http://localhost:9000'`
   - `$env:LLM_BASE_URL='http://localhost:11434'`
   - `$env:TOOLS_ENABLED='true'`
4. Run backend:
   - `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
5. Frontend setup (new terminal):
   - `cd frontend/web`
   - `npm install`
6. Export frontend env vars (PowerShell example):
   - `$env:NEXT_PUBLIC_API_BASE='http://localhost:8000/api/v1'`
   - `$env:NEXT_PUBLIC_WS_BASE='ws://localhost:8000/api/v1'`
7. Run frontend:
   - `npm run dev`
8. Open `http://localhost:3000`.

## Useful Commands
- Backend tests:
  - `cd services/core-ai && pytest -q`
- Frontend tests:
  - `cd frontend/web && npm test`
