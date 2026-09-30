"""Settings: precedence, validation, and what is deliberately not editable."""
import pytest

from app import settings as settings_mod
from app.config import Config


@pytest.fixture(autouse=True)
def clean_overrides():
    settings_mod._overrides.clear()
    yield
    settings_mod._overrides.clear()


def test_environment_is_the_default_and_an_override_beats_it(session, monkeypatch):
    monkeypatch.setenv("CV_URL", "https://from-the-stack.example/cv")
    assert settings_mod.raw("CV_URL") == "https://from-the-stack.example/cv"
    assert settings_mod.source("CV_URL") == "environment"

    settings_mod.set_value(session, "CV_URL", "https://set-in-the-ui.example/cv")
    assert settings_mod.raw("CV_URL") == "https://set-in-the-ui.example/cv"
    assert settings_mod.source("CV_URL") == "you"


def test_clearing_hands_the_setting_back_to_the_environment(session, monkeypatch):
    monkeypatch.setenv("CV_URL", "https://from-the-stack.example/cv")
    settings_mod.set_value(session, "CV_URL", "https://elsewhere.example/cv")
    settings_mod.clear(session, "CV_URL")
    assert settings_mod.raw("CV_URL") == "https://from-the-stack.example/cv"
    assert settings_mod.source("CV_URL") == "environment"


def test_a_judgement_falls_back_to_the_shipped_default(monkeypatch):
    monkeypatch.delenv("THIN_TEXT_CHARS", raising=False)
    assert settings_mod.raw("THIN_TEXT_CHARS") == "200"
    assert settings_mod.source("THIN_TEXT_CHARS") == "default"


def test_config_reads_through_settings(session, monkeypatch):
    monkeypatch.setenv("OPENALEX_AUTHOR_IDS", "A1111111111")
    assert Config().openalex_author_ids == ["A1111111111"]
    settings_mod.set_value(session, "OPENALEX_AUTHOR_IDS", "A2222222222,A3333333333")
    assert Config().openalex_author_ids == ["A2222222222", "A3333333333"]


def test_a_bad_value_is_refused_and_nothing_is_stored(session):
    with pytest.raises(ValueError):
        settings_mod.set_value(session, "CV_URL", "not-a-url")
    with pytest.raises(ValueError):
        settings_mod.set_value(session, "MATCH_RATIO", "7")
    with pytest.raises(ValueError):
        settings_mod.set_value(session, "OPENALEX_AUTHOR_IDS", "not-an-id")
    assert settings_mod.source("CV_URL") != "you"


def test_paths_and_secrets_are_not_editable(session):
    """A path that disagreed with its bind mount would be worse than one you
    cannot change; a key in the database would be in every volume backup."""
    for key in ("ZOTERO_SQLITE", "PROJECTS_DIR", "DATABASE_URL", "S2_API_KEY", "PORT"):
        assert key not in settings_mod.BY_KEY
        with pytest.raises(ValueError):
            settings_mod.set_value(session, key, "anything")


def test_a_threshold_is_read_at_call_time(session):
    from app.sources.zotero import ZoteroItem
    item = ZoteroItem(item_id=1, key="K", library_id=1, item_type="journalArticle")
    item.fields["title"] = "x" * 250
    assert item.thin is False
    settings_mod.set_value(session, "THIN_TEXT_CHARS", "500")
    assert item.thin is True, "no restart, no rebuild -- the next read sees it"


def test_an_unparseable_stored_value_degrades_to_the_code_default(session):
    settings_mod._overrides["THIN_TEXT_CHARS"] = "banana"
    assert settings_mod.num("THIN_TEXT_CHARS", 200) == 200


def test_changing_a_judgement_marks_the_ledger_stale(session):
    assert settings_mod.changed_at(session) is None
    settings_mod.set_value(session, "CONTACT_EMAIL", "me@example.com")
    assert settings_mod.changed_at(session) is None, "does not affect the ledger"
    settings_mod.set_value(session, "TITLE_RATIO", "0.85")
    assert settings_mod.changed_at(session) is not None


def test_the_api_reports_every_setting_with_its_origin(client):
    body = client.get("/api/settings").json()
    keys = {s["key"] for g in body["groups"] for s in g["settings"]}
    assert "CV_URL" in keys and "TITLE_RATIO" in keys
    assert "ZOTERO_SQLITE" not in keys and "S2_API_KEY" not in keys
    assert all(s["source"] in ("you", "environment", "default")
               for g in body["groups"] for s in g["settings"])
    assert "bind mounts" in body["not_editable"]["paths"]


def test_the_api_refuses_an_unknown_key(client):
    assert client.put("/api/settings/NOPE", json={"value": "x"}).status_code == 422


def test_the_api_round_trips_a_change(client):
    r = client.put("/api/settings/TITLE_RATIO", json={"value": "0.85"})
    assert r.status_code == 200
    assert r.json()["value"] == "0.85"
    assert "Rebuild" in r.json()["note"]

    body = client.get("/api/settings").json()
    row = next(s for g in body["groups"] for s in g["settings"]
               if s["key"] == "TITLE_RATIO")
    assert row["value"] == "0.85" and row["source"] == "you"

    client.delete("/api/settings/TITLE_RATIO")
    body = client.get("/api/settings").json()
    row = next(s for g in body["groups"] for s in g["settings"]
               if s["key"] == "TITLE_RATIO")
    assert row["source"] == "default" and row["value"] == "0.92"


def test_the_ledger_says_when_a_setting_moved_under_it(client):
    client.post("/api/ledger/rebuild")
    assert client.get("/api/ledger").json()["stale_settings"] is False
    client.put("/api/settings/THIN_TEXT_CHARS", json={"value": "300"})
    assert client.get("/api/ledger").json()["stale_settings"] is True


def test_the_cv_file_is_refused_in_favour_of_the_page(session):
    """Pointing at the PDF is the obvious mistake -- it is the thing actually
    called "my CV" -- and it used to fail silently three steps later, at the
    next rebuild, as "no public claim to compare against"."""
    with pytest.raises(ValueError) as exc:
        settings_mod.set_value(
            session, "CV_URL",
            "https://static1.squarespace.com/static/x/t/y/260812_CV_Name.pdf")
    assert "HTML page" in str(exc.value)
    for bad in ("https://x/cv.docx", "https://x/CV.PDF", "https://x/cv.pdf?dl=1"):
        with pytest.raises(ValueError):
            settings_mod.set_value(session, "CV_URL", bad)
    settings_mod.set_value(session, "CV_URL", "https://www.example.com/page-cv")
    assert settings_mod.raw("CV_URL").endswith("/page-cv")
