from app import db


def test_init_db_uses_sql_schema_bootstrap_when_available(monkeypatch):
    called = {"create_all": False}

    def fake_create_all(engine):
        called["create_all"] = True

    monkeypatch.setattr(db, "_apply_sql_schema_if_available", lambda: True)
    monkeypatch.setattr(db.SQLModel.metadata, "create_all", fake_create_all)

    db.init_db()

    assert called["create_all"] is False


def test_init_db_falls_back_to_create_all_when_schema_sql_not_used(monkeypatch):
    called = {"create_all": False, "ensure_schema": False}

    def fake_create_all(engine):
        called["create_all"] = True

    monkeypatch.setattr(db, "_apply_sql_schema_if_available", lambda: False)
    monkeypatch.setattr(db, "_ensure_default_schema", lambda: called.__setitem__("ensure_schema", True))
    monkeypatch.setattr(db.SQLModel.metadata, "create_all", fake_create_all)

    db.init_db()

    assert called["ensure_schema"] is True
    assert called["create_all"] is True
