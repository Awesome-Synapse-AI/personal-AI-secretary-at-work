# Core AI Service

## Overview
FastAPI service that runs the LangGraph-based agent workflow, exposes chat endpoints, and manages session memory in Redis.

## Local Run
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Environment
- `REDIS_URL` (default: redis://localhost:6379/0)
- `SESSION_TTL_SECONDS` (default: 86400)
- `AUTH_DISABLED` (default: true)
- `KEYCLOAK_REALM_URL` (default: http://localhost:8080/realms/ai-secretary)
- `KEYCLOAK_CLIENT_ID` (default: core-ai)
- `TOOLS_ENABLED` (default: true)
- `SERVICE_AUTH_TOKEN` (optional: bearer token for domain service calls)
- `LOG_LEVEL` (default: INFO)
- LLM classification:
  - `LLM_BASE_URL` (default: http://llm:11434)
  - `LLM_CHAT_PATH` (default: /v1/chat/completions)
  - `LLM_MODEL` (default: qwen3:0.6b)
  - `LLM_API_KEY` (optional)
  - `LLM_TIMEOUT_SECONDS` (default: 10)
- LangSmith tracing (optional):
  - `LANGCHAIN_TRACING_V2` (set to true)
  - `LANGCHAIN_API_KEY`
  - `LANGCHAIN_PROJECT`
  - `LANGCHAIN_ENDPOINT` (optional override)
- Google Calendar sync (optional):
  - `GOOGLE_CALENDAR_ENABLED` (default: false)
  - `GOOGLE_CALENDAR_CREDENTIALS` (path to service-account JSON)
  - `GOOGLE_CALENDAR_ID` (default: primary)
  - `GOOGLE_CALENDAR_TIMEZONE` (default: UTC; example: Asia/Bangkok)
  - `GOOGLE_CALENDAR_SUBJECT` (optional; Google Workspace user email for domain-wide delegation)

## Google Calendar Setup
1. Create a Google Cloud service account and enable the Google Calendar API for the project.
2. Download the service-account JSON key.
3. Decide your target calendar:
   - Shared calendar: share the calendar with the service-account email and grant `Make changes to events`.
   - User primary calendar (Workspace): enable domain-wide delegation and set `GOOGLE_CALENDAR_SUBJECT` to the user email.
4. For Docker Compose:
   - Copy the key to `infra/docker/secrets/google_calendar_credentials.json`.
   - In `infra/docker/.env`, set `GOOGLE_CALENDAR_ENABLED=true`.
   - Set `GOOGLE_CALENDAR_ID` (`primary` or a specific calendar ID) and `GOOGLE_CALENDAR_TIMEZONE`.
5. Restart `core-ai`.

The API writes events for workspace bookings, leave requests, and travel requests. If Google sync fails, the local DB event is still created.
