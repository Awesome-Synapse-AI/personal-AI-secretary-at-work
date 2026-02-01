import asyncio
from types import SimpleNamespace

from app import llm_client
from app.config import settings


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception("http error")


class _FakeAsyncClient:
    def __init__(self, responder):
        self._responder = responder
        self.last_kwargs = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json=None, headers=None):
        self.last_kwargs = SimpleNamespace(url=url, json=json, headers=headers)
        return self._responder(json)


def test_call_llm_json_success(monkeypatch):
    def responder(payload):
        assert payload["model"] == settings.llm_model == "qwen3:0.6b"
        return _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"plan":"ok","steps":2}',
                        }
                    }
                ]
            }
        )

    fake_client = _FakeAsyncClient(responder)
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda timeout: fake_client)

    result = asyncio.run(llm_client.call_llm_json("sys", "user", max_tokens=32))
    assert result == {"plan": "ok", "steps": 2}


def test_call_llm_json_handles_http_error(monkeypatch):
    def responder(payload):
        raise Exception("boom")

    fake_client = _FakeAsyncClient(responder)
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda timeout: fake_client)

    result = asyncio.run(llm_client.call_llm_json("sys", "user", max_tokens=16))
    assert result is None


def test_call_llm_json_repairs_non_json(monkeypatch):
    # returns text with embedded json object; should be extracted
    def responder(payload):
        return _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "Here you go: {\"foo\":1,\"bar\":\"baz\"} thanks",
                        }
                    }
                ]
            }
        )

    fake_client = _FakeAsyncClient(responder)
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda timeout: fake_client)

    result = asyncio.run(llm_client.call_llm_json("sys", "user", max_tokens=16))
    assert result == {"foo": 1, "bar": "baz"}
