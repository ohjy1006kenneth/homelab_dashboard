import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.main import app
import backend.database as database
import backend.routers.agents as agents
import backend.routers.newsletters as newsletters
from cron import run_news_cycle as canonical_cycle

SPEC = importlib.util.spec_from_file_location("run_news_cycle", Path(__file__).parents[1] / "cron/run_news_cycle.py")
cycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cycle)


@pytest.fixture
def fake_config(tmp_path, monkeypatch):
    config = {
        "newsletter_sources": [{"name": "Chip Feed", "rss": "fake://chips", "category": "Semiconductor"}],
        "news_retention": {"max_items": 2, "display_limit": 10},
        "preferences": {"news": {"curated_categories": ["Semiconductor", "Stocks", "AI"]}},
        "news_summarizer": {"profile": "news-brief"},
    }
    config_path = tmp_path / "dashboard.config.json"
    config_path.write_text(json.dumps(config))
    db_path = tmp_path / "dashboard.db"
    monkeypatch.setattr(cycle, "CONFIG_PATH", config_path)
    monkeypatch.setattr(cycle, "DB_PATH", db_path)
    return config_path, db_path


def parser(_url):
    return SimpleNamespace(bozo=False, entries=[
        {"title": "Chip launch", "link": "https://example/chip", "published": "2026-01-01", "summary": "First sentence. Second sentence."},
        {"title": "Chip roundup", "link": "https://example/roundup", "published": "2026-01-02", "summary": "A roundup of news."},
    ])


def test_cycle_fallback_dedupes_and_excludes_roundups(fake_config, monkeypatch):
    _, db_path = fake_config
    monkeypatch.setattr(cycle, "hermes_generate", lambda items, config: (_ for _ in ()).throw(RuntimeError("unavailable")))
    first = cycle.run_cycle(feed_parser=parser)
    second = cycle.run_cycle(feed_parser=parser)
    assert first["fetched_new"] == 2
    assert second["fetched_new"] == 0
    assert first["brief_items"] == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select count(*) from newsletter_item").fetchone()[0] == 2
        assert "\n\n" not in conn.execute("select summary from newsletter_item limit 1").fetchone()[0]


def test_dry_run_does_not_create_database(fake_config, monkeypatch):
    _, db_path = fake_config
    result = cycle.run_cycle(dry_run=True, feed_parser=parser)
    assert result["dry_run"] is True
    assert result["planned_items"] == 2
    assert not db_path.exists()


def test_dry_run_preserves_prepopulated_database_bytes(fake_config, monkeypatch):
    _, db_path = fake_config
    with sqlite3.connect(db_path) as conn:
        cycle.ensure_table(conn)
        conn.execute("INSERT INTO newsletter_item (id, source, title, url, published_at, summary, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     ("existing", "Feed", "Existing", "https://example/existing", "2026-01-01", "Summary", "2026-01-01"))
        conn.execute("INSERT INTO newsletter_brief (id, generated_at, source, headline, items_json) VALUES (1, ?, ?, ?, ?)",
                     ("2026-01-01", "news-brief", "Daily News Brief", "[]"))
        conn.commit()
    before = db_path.read_bytes()
    result = cycle.run_cycle(dry_run=True, feed_parser=parser)
    assert result["dry_run"] is True
    assert db_path.read_bytes() == before
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM newsletter_item").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM newsletter_brief").fetchone()[0] == 1


def test_curation_empty_contract(tmp_path, monkeypatch):
    db_path = tmp_path / "empty.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    response = TestClient(app).get("/api/newsletters/curation")
    assert response.status_code == 200
    assert response.json() == {"generated_at": None, "source": "", "headline": "", "items": []}


def test_curation_returns_persisted_brief(tmp_path, monkeypatch):
    db_path = tmp_path / "brief.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE newsletter_item (id TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT, published_at TEXT, summary TEXT, read INTEGER DEFAULT 0, fetched_at TEXT)")
        conn.execute("CREATE TABLE newsletter_brief (id INTEGER PRIMARY KEY, generated_at TEXT, source TEXT, headline TEXT, items_json TEXT)")
        conn.execute("INSERT INTO newsletter_brief VALUES (1, '2026-01-01T00:00:00Z', 'news-brief', 'Daily News Brief', ?)", (json.dumps([{"category": "AI", "title": "A", "url": "https://example/a", "source": "Feed", "published_at": "2026-01-01", "summary": "One.\n\nTwo."}]),))
    response = TestClient(app).get("/api/newsletters/curation")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"generated_at", "source", "headline", "items"}
    assert payload["generated_at"] == "2026-01-01T00:00:00Z"
    assert payload["source"] == "news-brief"
    assert payload["headline"] == "Daily News Brief"
    assert payload["items"]
    item = payload["items"][0]
    assert set(item) == {"category", "title", "url", "source", "published_at", "summary"}
    assert item == {"category": "AI", "title": "A", "url": "https://example/a", "source": "Feed",
                    "published_at": "2026-01-01", "summary": "One.\n\nTwo."}
    assert len(item["summary"].split("\n\n")) == 2


def test_python_agent_uses_active_interpreter_and_shell_behavior_is_unchanged(tmp_path):
    assert agents._command_for_script(tmp_path / "job.py") == [sys.executable, str(tmp_path / "job.py")]
    assert agents._command_for_script(tmp_path / "job.sh") == ["bash", str(tmp_path / "job.sh")]


def test_valid_hermes_success_uses_supported_profile_argv(fake_config, monkeypatch):
    captured = {}

    def run(argv, **kwargs):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
            "summaries": {"a": "Latest summary."},
            "items": [{"id": "a", "category": "Semiconductor", "title": "fabricated", "url": "https://bad", "summary": "One concise story."}],
        }))

    monkeypatch.setattr(cycle.subprocess, "run", run)
    result = cycle.hermes_generate([{"id": "a"}], {"news_summarizer": {"profile": "news-brief"}})
    assert result["items"]
    assert captured["argv"][:4] == ["hermes", "--profile", "news-brief", "chat"]
    assert "-s" not in captured["argv"] and "--skills" not in captured["argv"]


def test_invalid_json_schema_and_ungrounded_output_fall_back_with_errors(fake_config, monkeypatch):
    _, db_path = fake_config
    monkeypatch.setattr(cycle, "hermes_generate", lambda items, config: {"summaries": {"unknown": "fake"}, "items": []})
    result = cycle.run_cycle(feed_parser=parser)
    assert result["errors"] and result["errors"][0]["stage"] == "hermes"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select count(*) from newsletter_item").fetchone()[0] == 2
        assert conn.execute("select items_json from newsletter_brief").fetchone()[0].find("https://bad") == -1


def test_invalid_raw_hermes_json_falls_back_with_explicit_error(fake_config, monkeypatch):
    _, db_path = fake_config
    monkeypatch.setattr(cycle.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout="not json"))
    result = cycle.run_cycle(feed_parser=parser)
    assert any(error["stage"] == "hermes" for error in result["errors"])
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select count(*) from newsletter_item").fetchone()[0] == 2
        assert conn.execute("select count(*) from newsletter_brief").fetchone()[0] == 1


def test_non_object_raw_hermes_json_is_rejected(fake_config, monkeypatch):
    monkeypatch.setattr(cycle.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout="[]"))
    with pytest.raises(ValueError, match="schema"):
        cycle.hermes_generate([{"id": "a"}], {"news_summarizer": {"profile": "news-brief"}})


def test_wrapped_hermes_json_is_rejected_and_persists_deterministic_fallback(fake_config, monkeypatch):
    _, db_path = fake_config

    def run(argv, **kwargs):
        prompt = json.loads(argv[argv.index("-q") + 1])
        items = prompt["input"]
        generated = {
            "summaries": {str(item["id"]): "Generated summary." for item in items},
            "items": [{"id": items[0]["id"], "category": "Semiconductor", "summary": "Generated curation."}],
        }
        return SimpleNamespace(returncode=0, stderr="", stdout=f"prefix {json.dumps(generated)} suffix")

    monkeypatch.setattr(cycle.subprocess, "run", run)
    result = cycle.run_cycle(feed_parser=parser)

    assert any(error["stage"] == "hermes" for error in result["errors"])
    with sqlite3.connect(db_path) as conn:
        latest = conn.execute("SELECT title, summary FROM newsletter_item WHERE title = 'Chip launch'").fetchone()
        brief = json.loads(conn.execute("SELECT items_json FROM newsletter_brief WHERE id = 1").fetchone()[0])
    assert latest == ("Chip launch", "First sentence. Second sentence.")
    assert brief == [{
        "category": "Semiconductor",
        "title": "Chip launch",
        "url": "https://example/chip",
        "source": "Chip Feed",
        "published_at": "2026-01-01",
        "summary": "First sentence.\n\nSecond sentence.",
    }]


@pytest.mark.parametrize("field,value", [
    ("summary", 7), ("summary", []), ("summary", {}), ("summary", None),
    ("category", 7), ("category", []), ("category", {}), ("category", None),
    ("id", 7), ("source_id", []), ("url", {}),
])
def test_malformed_generated_fields_are_rejected(field, value):
    source = {"id": "a", "category": "Semiconductor", "title": "Chip", "url": "https://example/chip",
              "source": "Feed", "published_at": "2026-01-01"}
    candidate = {"id": "a", "category": "Semiconductor", "summary": "One. Two."}
    if field in {"id", "source_id", "url"}:
        candidate.pop("id", None)
        candidate.pop("source_id", None)
        candidate.pop("url", None)
    else:
        candidate.pop(field, None)
    candidate[field] = value
    with pytest.raises(ValueError):
        cycle.validate_generated({"summaries": {"a": "Latest."}, "items": [candidate]}, [source], ["Semiconductor"])


@pytest.mark.parametrize("field,value", [
    ("latest", 7), ("latest", []), ("latest", {}), ("latest", None),
    ("summary", 7), ("summary", []), ("summary", {}), ("summary", None),
    ("category", 7), ("category", []), ("category", {}), ("category", None),
    ("id", 7), ("source_id", []), ("url", {}),
])
def test_malformed_generated_fields_fall_back_atomically_without_persisting_junk(fake_config, monkeypatch, field, value):
    _, db_path = fake_config

    def generated(items, config):
        response = {
            "summaries": {str(item["id"]): "Trusted latest summary." for item in items},
            "items": [{"id": items[0]["id"], "category": "Semiconductor", "summary": "Trusted first. Trusted second."}],
        }
        if field == "latest":
            response["summaries"][str(items[0]["id"])] = value
        elif field in {"id", "source_id", "url"}:
            response["items"][0].pop("id", None)
            response["items"][0][field] = value
        else:
            response["items"][0][field] = value
        return response

    monkeypatch.setattr(cycle, "hermes_generate", generated)
    result = cycle.run_cycle(feed_parser=parser)

    assert any(error["stage"] == "hermes" for error in result["errors"])
    with sqlite3.connect(db_path) as conn:
        latest = [row[0] for row in conn.execute("SELECT summary FROM newsletter_item")]
        brief = json.loads(conn.execute("SELECT items_json FROM newsletter_brief").fetchone()[0])
    assert all("Trusted" not in summary for summary in latest)
    assert all("Trusted" not in item["summary"] for item in brief)


def test_valid_generated_response_is_canonical_and_persisted_with_supported_argv(fake_config, monkeypatch):
    _, db_path = fake_config

    captured = {}

    def run(argv, **kwargs):
        captured["argv"] = argv
        prompt = json.loads(argv[argv.index("-q") + 1])
        items = prompt["input"]
        item = items[0]
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
            "summaries": {str(row["id"]): "Latest one. Still latest." for row in items},
            "items": [{"id": item["id"], "category": "Semiconductor", "title": "fabricated", "url": "https://bad",
                       "source": "bad", "published_at": "bad", "summary": "First. Second."}],
        }))

    monkeypatch.setattr(cycle.subprocess, "run", run)
    result = cycle.run_cycle(feed_parser=parser)
    assert result["errors"] == []
    assert captured["argv"][:4] == ["hermes", "--profile", "news-brief", "chat"]
    assert "-s" not in captured["argv"] and "--skills" not in captured["argv"]
    with sqlite3.connect(db_path) as conn:
        latest = conn.execute("select title, url, source, published_at, summary from newsletter_item order by id limit 1").fetchone()
        brief = json.loads(conn.execute("select items_json from newsletter_brief").fetchone()[0])
    assert latest[4] == "Latest one. Still latest."
    assert brief[0]["title"] == "Chip launch"
    assert brief[0]["url"] == "https://example/chip"
    assert brief[0]["source"] == "Chip Feed"
    assert brief[0]["published_at"] == "2026-01-01"
    assert len(brief[0]["summary"].split("\n\n")) == 2


def test_duplicate_generated_category_falls_back_and_stores_unique_categories(fake_config, monkeypatch):
    _, db_path = fake_config

    def generated(items, config):
        return {
            "summaries": {str(item["id"]): "Generated latest." for item in items},
            "items": [
                {"id": items[0]["id"], "category": "Semiconductor", "summary": "Generated first."},
                {"id": items[0]["id"], "category": "Semiconductor", "summary": "Generated duplicate."},
            ],
        }

    monkeypatch.setattr(cycle, "hermes_generate", generated)
    result = cycle.run_cycle(feed_parser=parser)
    assert any(error["stage"] == "hermes" for error in result["errors"])
    with sqlite3.connect(db_path) as conn:
        brief = json.loads(conn.execute("SELECT items_json FROM newsletter_brief").fetchone()[0])
    categories = [item["category"] for item in brief]
    assert len(categories) == len(set(categories))
    assert categories == ["Semiconductor"]


def test_duplicate_generated_category_is_rejected():
    items = [{"id": "a", "category": "Semiconductor", "title": "Chip", "url": "https://example/chip",
              "source": "Feed", "published_at": "2026-01-01"}]
    generated = {"summaries": {"a": "Latest."}, "items": [
        {"id": "a", "category": "Semiconductor", "summary": "First."},
        {"id": "a", "category": "Semiconductor", "summary": "Second."},
    ]}
    with pytest.raises(ValueError, match="categories"):
        cycle.validate_generated(generated, items, ["Semiconductor"])


def test_lowercase_source_categories_are_canonicalized_in_fallback(fake_config, monkeypatch):
    config_path, db_path = fake_config
    config = json.loads(config_path.read_text())
    config["newsletter_sources"] = [
        {"name": "Stocks", "rss": "fake://stocks", "category": "stocks"},
        {"name": "AI", "rss": "fake://ai", "category": "ai"},
    ]
    config["preferences"]["news"]["curated_categories"] = ["Stocks", "AI"]
    config_path.write_text(json.dumps(config))
    monkeypatch.setattr(cycle, "hermes_generate", lambda items, config: (_ for _ in ()).throw(RuntimeError("offline")))

    def two(url):
        category = url.rsplit("/", 1)[-1]
        return SimpleNamespace(bozo=False, entries=[{"title": category, "link": f"https://example/{category}", "published": category, "summary": "One. Two."}])

    result = cycle.run_cycle(feed_parser=two)
    assert result["brief_items"] == 2
    with sqlite3.connect(db_path) as conn:
        brief = json.loads(conn.execute("SELECT items_json FROM newsletter_brief").fetchone()[0])
    assert [item["category"] for item in brief] == ["Stocks", "AI"]


@pytest.mark.parametrize("generated", [
    {"summaries": {"a": "Latest."}, "items": [{"id": "a", "category": "AI", "summary": "First."}]},
    {"summaries": {"a": "Latest.", "b": "Latest."}, "items": [
        {"id": "a", "category": "Semiconductor", "summary": "First."},
        {"id": "a", "category": "AI", "summary": "Second."},
    ]},
])
def test_generated_wrong_category_or_duplicate_grounding_is_rejected(generated):
    items = [
        {"id": "a", "category": "Stocks", "title": "Stock", "url": "https://example/stock", "source": "Feed", "published_at": "2026-01-01"},
        {"id": "b", "category": "AI", "title": "AI", "url": "https://example/ai", "source": "Feed", "published_at": "2026-01-01"},
    ]
    with pytest.raises(ValueError):
        cycle.validate_generated(generated, items, ["Stocks", "AI"])


def test_manual_fetch_uses_canonical_cycle_and_returns_compatible_report(fake_config, monkeypatch):
    config_path, db_path = fake_config
    monkeypatch.setattr(newsletters, "CONFIG_PATH", config_path)
    monkeypatch.setattr(canonical_cycle, "CONFIG_PATH", config_path)
    monkeypatch.setattr(canonical_cycle, "DB_PATH", db_path)
    monkeypatch.setattr(canonical_cycle, "feedparser", SimpleNamespace(parse=parser))
    monkeypatch.setattr(canonical_cycle, "hermes_generate", lambda items, config: (_ for _ in ()).throw(RuntimeError("offline")))
    response = TestClient(app).post("/api/newsletters/fetch")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True and payload["fetched"] == 2
    assert any(error["stage"] == "hermes" for error in payload["errors"])
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM newsletter_item").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM newsletter_brief").fetchone()[0] == 1


def test_manual_fetch_persists_agent_success_and_ordered_curation(fake_config, monkeypatch):
    config_path, db_path = fake_config
    monkeypatch.setattr(newsletters, "CONFIG_PATH", config_path)
    monkeypatch.setattr(canonical_cycle, "CONFIG_PATH", config_path)
    monkeypatch.setattr(canonical_cycle, "DB_PATH", db_path)
    monkeypatch.setattr(canonical_cycle, "feedparser", SimpleNamespace(parse=parser))

    def generated(items, config):
        return {
            "summaries": {str(item["id"]): "Agent latest summary." for item in items},
            "items": [{"id": items[0]["id"], "category": "Semiconductor", "summary": "First paragraph. Second paragraph."}],
        }

    monkeypatch.setattr(canonical_cycle, "hermes_generate", generated)
    response = TestClient(app).post("/api/newsletters/fetch")
    assert response.status_code == 200
    assert response.json()["errors"] == []
    with sqlite3.connect(db_path) as conn:
        latest = conn.execute("SELECT summary FROM newsletter_item WHERE id = ?", (cycle.item_id("Chip Feed", "https://example/chip", "Chip launch"),)).fetchone()[0]
        brief = json.loads(conn.execute("SELECT items_json FROM newsletter_brief").fetchone()[0])
    assert latest == "Agent latest summary."
    assert [item["category"] for item in brief] == ["Semiconductor"]
    assert len(brief[0]["summary"].split("\n\n")) == 2


def test_manual_fetch_isolates_broken_source(fake_config, monkeypatch):
    config_path, db_path = fake_config
    config = json.loads(config_path.read_text())
    config["newsletter_sources"].append({"name": "Broken", "rss": "fake://broken", "category": "AI"})
    config_path.write_text(json.dumps(config))
    monkeypatch.setattr(newsletters, "CONFIG_PATH", config_path)
    monkeypatch.setattr(canonical_cycle, "CONFIG_PATH", config_path)
    monkeypatch.setattr(canonical_cycle, "DB_PATH", db_path)
    monkeypatch.setattr(canonical_cycle, "hermes_generate", lambda items, config: (_ for _ in ()).throw(RuntimeError("offline")))

    def mixed(url):
        if url.endswith("broken"):
            raise RuntimeError("broken feed")
        return parser(url)

    monkeypatch.setattr(canonical_cycle, "feedparser", SimpleNamespace(parse=mixed))
    response = TestClient(app).post("/api/newsletters/fetch")
    assert response.status_code == 200
    payload = response.json()
    assert payload["fetched"] == 2
    assert any(error["source"] == "Broken" for error in payload["errors"])
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM newsletter_item").fetchone()[0] == 2


def test_feed_errors_are_isolated_and_healthy_source_persists(fake_config, monkeypatch):
    config_path, db_path = fake_config
    config = json.loads(config_path.read_text())
    config["newsletter_sources"].append({"name": "Broken", "rss": "fake://broken", "category": "AI"})
    config_path.write_text(json.dumps(config))

    def mixed(url):
        if url.endswith("broken"):
            raise RuntimeError("broken feed")
        return parser(url)

    monkeypatch.setattr(cycle, "hermes_generate", lambda items, config: (_ for _ in ()).throw(RuntimeError("offline")))
    result = cycle.run_cycle(feed_parser=mixed)
    assert any(error["source"] == "Broken" for error in result["errors"])
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select count(*) from newsletter_item").fetchone()[0] == 2


def test_retention_caps_rows_and_categories_follow_configured_order(fake_config, monkeypatch):
    config_path, db_path = fake_config
    config = json.loads(config_path.read_text())
    config["news_retention"]["max_items"] = 2
    config["newsletter_sources"] = [
        {"name": "AI", "rss": "fake://ai", "category": "AI"},
        {"name": "Stocks", "rss": "fake://stocks", "category": "Stocks"},
        {"name": "Semi", "rss": "fake://semi", "category": "Semiconductor"},
    ]
    config["preferences"]["news"]["curated_categories"] = ["AI", "Stocks", "Semiconductor"]
    config_path.write_text(json.dumps(config))

    def three(url):
        name = url.rsplit("/", 1)[-1]
        return SimpleNamespace(bozo=False, entries=[{"title": name, "link": f"https://example/{name}", "published": name, "summary": "One word"}])

    monkeypatch.setattr(cycle, "hermes_generate", lambda items, config: (_ for _ in ()).throw(RuntimeError("offline")))
    result = cycle.run_cycle(feed_parser=three)
    assert result["brief_items"] == 3
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select count(*) from newsletter_item").fetchone()[0] == 2
        categories = [row[0] for row in conn.execute("select items_json from newsletter_brief")]
    assert json.loads(categories[0])[0]["category"] == "AI"


def test_paragraph_invariants_include_one_word_fallback():
    assert len([part for part in cycle.paragraphs("Latest now", 1).split("\n\n") if part.strip()]) == 1
    two = cycle.paragraphs("word", 2).split("\n\n")
    assert len(two) == 2 and all(part.strip() for part in two)
