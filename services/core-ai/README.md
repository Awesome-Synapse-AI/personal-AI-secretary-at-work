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
- `TOOLS_ENABLED` (default: false)
- `SERVICE_AUTH_TOKEN` (optional: bearer token for domain service calls)
- `LOG_LEVEL` (default: INFO)
- LLM classification:
  - `LLM_BASE_URL` (default: http://llm:80)
  - `LLM_CHAT_PATH` (default: /v1/chat/completions)
  - `LLM_MODEL` (default: Qwen/Qwen3-0.6B)
  - `LLM_API_KEY` (optional)
  - `LLM_TIMEOUT_SECONDS` (default: 10)
- LangSmith tracing (optional):
  - `LANGCHAIN_TRACING_V2` (set to true)
  - `LANGCHAIN_API_KEY`
  - `LANGCHAIN_PROJECT`
  - `LANGCHAIN_ENDPOINT` (optional override)
