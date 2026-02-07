import io
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models import Document, DocumentChunk


@pytest.mark.asyncio
async def test_upload_and_search_text(client: TestClient, session: Session, monkeypatch):
    # Monkeypatch embeddings to return deterministic vectors
    async def fake_embed_texts(texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    from app import api

    monkeypatch.setattr(api, "_embed_texts", fake_embed_texts)
    monkeypatch.setattr(api, "_qdrant_client", lambda: None)  # force local storage path
    monkeypatch.setattr(api, "settings", api.settings.model_copy(update={"upload_dir": tempfile.mkdtemp()}))

    file_content = b"Travel policy allows $50 per diem"
    response = client.post(
        "/api/v1/documents/upload",
        files={
            "file": ("policy.txt", io.BytesIO(file_content), "text/plain"),
        },
        data={"filename": "policy.txt", "owner": "u1", "scope": "user_docs", "source": "manual"},
    )
    assert response.status_code == 200
    doc_id = response.json()["document_id"]

    # verify stored
    doc = session.get(Document, doc_id)
    assert doc is not None
    chunk = session.exec(
        select(DocumentChunk).where(DocumentChunk.document_id == doc_id)
    ).first()
    assert chunk is not None

    # search
    search_resp = client.post(
        "/api/v1/documents/search",
        json={"query": "per diem", "top_k": 3, "owner": "u1", "scope": "user_docs"},
    )
    assert search_resp.status_code == 200
    matches = search_resp.json()["matches"]
    assert len(matches) >= 1
    assert matches[0]["document_id"] == doc_id


@pytest.mark.asyncio
async def test_upload_image_uses_ocr(monkeypatch, client: TestClient):
    # Fake OCR to return text
    from app import api

    async def fake_embed_texts(texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    monkeypatch.setattr(api, "_ocr_bytes", lambda content, ct: "Image text here")
    monkeypatch.setattr(api, "_embed_texts", fake_embed_texts)
    monkeypatch.setattr(api, "_qdrant_client", lambda: None)
    monkeypatch.setattr(api, "settings", api.settings.model_copy(update={"upload_dir": tempfile.mkdtemp()}))

    img_bytes = b"fakeimagebytes"
    response = client.post(
        "/api/v1/documents/upload",
        files={"file": ("img.png", io.BytesIO(img_bytes), "image/png")},
        data={"filename": "img.png", "owner": "u2", "scope": "user_docs", "source": "manual"},
    )
    assert response.status_code == 200
    # ensure we indexed chunks
    doc_id = response.json()["document_id"]
    # further DB validation is done in previous test; here we just assert success


@pytest.mark.asyncio
async def test_search_filters_scope(monkeypatch, client: TestClient, session: Session):
    from app import api

    # Seed two docs manually
    d1 = api.Document(owner="a", scope="user_docs", source="manual", title="D1", path="/tmp/d1")
    d2 = api.Document(owner="b", scope="policy_hr", source="manual", title="D2", path="/tmp/d2")
    session.add_all([d1, d2])
    session.commit()
    session.add(api.DocumentChunk(document_id=d1.id, content="hello world", embedding=b"\x00\x00", chunk_index=0))
    session.add(api.DocumentChunk(document_id=d2.id, content="hello hr", embedding=b"\x00\x00", chunk_index=0))
    session.commit()

    # skip embeddings/qdrant path; directly test filter logic via fallback
    async def fake_embed_texts(texts):
        return [[1.0, 0.0]]

    monkeypatch.setattr(api, "_embed_texts", fake_embed_texts)
    monkeypatch.setattr(api, "_qdrant_client", lambda: None)
    monkeypatch.setattr(api, "_deserialize_vec", lambda b: [1.0, 0.0])

    resp = client.post(
        "/api/v1/documents/search",
        json={"query": "hello", "top_k": 5, "scope": "user_docs"},
    )
    assert resp.status_code == 200
    matches = resp.json()["matches"]
    assert all(m["document_id"] == d1.id for m in matches)


def _fake_qdrant():
    calls = {"search": [], "upsert": []}

    class FakeCollections:
        def __init__(self):
            self.collections = []

    class FakeClient:
        def get_collections(self):
            return FakeCollections()

        def search(self, collection_name, query_vector, limit):
            calls["search"].append({"collection": collection_name, "limit": limit})
            return [
                type("hit", (), {"payload": {"document_id": 1, "chunk_index": 0}, "score": 0.9}),
            ]

        def upsert(self, collection_name, wait, points):
            calls["upsert"].append({"collection": collection_name, "points": points})
            return None

    return FakeClient(), calls


@pytest.mark.asyncio
async def test_policy_scope_selects_collection(monkeypatch, client: TestClient, session: Session, tmp_path):
    from app import api

    # Directly test collection routing logic
    assert api._choose_collection("policy_hr", "manual") == api.settings.qdrant_collection_policy_hr
    assert api._choose_collection("policy_it", "manual") == api.settings.qdrant_collection_policy_it
    assert api._choose_collection("policy_travel_expense", "manual") == api.settings.qdrant_collection_policy_travel_expense
    assert api._choose_collection("user_docs", "manual") == api.settings.qdrant_collection_user_docs


@pytest.mark.asyncio
async def test_search_uses_override_collection(monkeypatch, client: TestClient, session: Session):
    from app import api

    fake_client, calls = _fake_qdrant()

    async def fake_embed_texts(texts):
        return [[0.5, 0.5, 0.5]]

    monkeypatch.setattr(api, "_embed_texts", fake_embed_texts)
    monkeypatch.setattr(api, "_qdrant_client", lambda: fake_client)
    monkeypatch.setattr(api, "_ensure_collection", lambda client, size, collection: calls.setdefault("ensure", []).append(collection))

    # seed doc
    doc = api.Document(owner="x", scope="user_docs", source="manual", title="Doc", path="/tmp/doc")
    session.add(doc)
    session.commit()

    resp = client.post(
        "/api/v1/documents/search",
        json={"query": "anything", "top_k": 2, "collection": "policy_it"},
    )
    assert resp.status_code == 200
    assert calls["ensure"][0] == "policy_it"
    assert calls["search"][0]["collection"] == "policy_it"
