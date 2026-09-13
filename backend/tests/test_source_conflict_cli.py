"""Offline operator workflow: explicit setup, bounded scope, no runtime startup."""

import json

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.db import Database
from app.models import SettingEntry
from app.tools import source_conflicts as cli


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'audit.sqlite3'}",
        redis_url="",
        qdrant_url="",
        tei_url="",
        tei_rerank_url="",
        llm_base_url="",
        llm_api_key="",
        llm_model="",
        s3_endpoint="",
        s3_access_key="",
        s3_secret_key="",
        stt_url="",
        tts_url="",
        toxicity_url="",
        docparse_url="",
        zakupki_mcp_url="",
    )


async def test_list_on_missing_database_does_not_create_it(settings, tmp_path):
    with pytest.raises(OperationalError):
        await cli.execute(cli.build_parser().parse_args(["list"]), settings)
    assert not (tmp_path / "audit.sqlite3").exists()
    assert not settings.data_dir.exists()


async def test_init_then_read_only_list_does_not_seed_settings_or_call_models(settings, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Read-only commands must not initialize model clients")

    monkeypatch.setattr(cli, "create_embedder", forbidden)
    setup = await cli.execute(cli.build_parser().parse_args(["init-schema"]), settings)
    assert setup["status"] == "completed"
    for kind in ("jobs", "conflicts"):
        result = await cli.execute(cli.build_parser().parse_args(["list", "--kind", kind]), settings)
        assert result["summary"]["items"] == []
        assert set(result) == {"status", "summary", "next_actions", "artifacts"}
    db = Database(settings.database_url)
    try:
        async with db.session_factory() as session:
            assert not (await session.execute(select(SettingEntry))).scalars().all()
        async with db.engine.connect() as connection:
            tables = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
            assert "kb_articles" in tables
    finally:
        await db.dispose()


@pytest.mark.parametrize(
    "arguments",
    [
        ["audit"],
        ["audit", "--article-id", "a"],
        ["audit", "--article-id", "a", "--article-id", "a"],
        ["audit", "--article-id", "a", "--article-id", "b", "--max-calls", "0"],
        ["audit", "--article-id", "a", "--article-id", "b", "--max-articles", "201"],
        ["list", "--limit", "0"],
        ["list", "--offset", "-1"],
    ],
)
async def test_invalid_limits_fail_before_opening_database(arguments, settings, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid arguments must fail before accessing the database")

    monkeypatch.setattr(cli, "_open_database", forbidden)
    with pytest.raises((cli.CliError, SystemExit)):
        await cli.execute(cli.build_parser().parse_args(arguments), settings)


async def test_show_missing_id_is_actionable(settings):
    await cli.execute(cli.build_parser().parse_args(["init-schema"]), settings)
    with pytest.raises(cli.CliError, match="not_found"):
        await cli.execute(cli.build_parser().parse_args(["show", "missing"]), settings)


async def test_audit_without_llm_fails_without_job(settings):
    await cli.execute(cli.build_parser().parse_args(["init-schema"]), settings)
    with pytest.raises(cli.CliError, match="llm_not_configured"):
        await cli.execute(
            cli.build_parser().parse_args(["audit", "--article-id", "a", "--article-id", "b"]),
            settings,
        )
    result = await cli.execute(cli.build_parser().parse_args(["list"]), settings)
    assert result["summary"]["items"] == []


async def test_llm_configuration_uses_saved_values_without_seeding(settings):
    await cli.execute(cli.build_parser().parse_args(["init-schema"]), settings)
    settings.llm_base_url = "https://environment.invalid/v1"
    settings.llm_model = "environment-model"
    settings.llm_api_key = "environment-secret"
    db = Database(settings.database_url)
    try:
        async with db.session_factory() as session:
            session.add(SettingEntry(key="llm.model", value={"v": "saved-model"}, category="llm"))
            await session.commit()
        config = await cli._load_llm_config(db.session_factory, settings)
        assert config.get("llm.model") == "saved-model"
        assert config.get("llm.api_key") == "environment-secret"
        async with db.session_factory() as session:
            assert len((await session.execute(select(SettingEntry))).scalars().all()) == 1
    finally:
        await db.dispose()


def test_machine_error_never_discloses_exception_credentials(monkeypatch, capsys):
    async def failed(*args, **kwargs):
        raise RuntimeError("https://admin:password@database.invalid api_key=SECRET")

    monkeypatch.setattr(cli, "execute", failed)
    assert cli.main(["list"]) == 1
    raw = capsys.readouterr().out
    assert "SECRET" not in raw and "password" not in raw and "database.invalid" not in raw
    result = json.loads(raw)
    assert result["status"] == "failed"
    assert result["summary"]["error"] == "operation_failed"


async def test_synthetic_audit_list_show_and_resume(settings, monkeypatch):
    """Real SQLite registry + auditor + candidate retrieval; model verdict is a stub."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.models import KBArticle
    from app.schemas.source_conflicts import PairAssessment
    from app.services import source_conflict_detector

    await cli.execute(cli.build_parser().parse_args(["init-schema"]), settings)
    db = Database(settings.database_url)
    try:
        async with db.session_factory() as session:
            session.add_all(
                [
                    KBArticle(id="a", question="Какой предел файла?", answer="До 10 МБ.", category="Файлы"),
                    KBArticle(id="b", question="Какой предел файла?", answer="До 20 МБ.", category="Файлы"),
                ]
            )
            await session.commit()
    finally:
        await db.dispose()
    fake = SimpleNamespace(
        version="synthetic-v1",
        assess=AsyncMock(
            return_value=PairAssessment(
                same_fact="yes",
                conditions="overlap",
                claims="incompatible",
                material="yes",
                fact="Предел файла",
                left_quote="До 10 МБ.",
                right_quote="До 20 МБ.",
                explanation="Разные пределы для одной операции.",
            )
        ),
        aclose=AsyncMock(),
    )
    monkeypatch.setattr(source_conflict_detector, "create_conflict_detector", lambda config: fake)
    settings.llm_base_url = "https://synthetic.invalid/v1"
    settings.llm_model = "fake-model"
    result = await cli.execute(
        cli.build_parser().parse_args(["audit", "--article-id", "a", "--article-id", "b"]), settings
    )
    assert result["status"] == "completed"
    job = result["summary"]["job"]
    assert job["report"]["conflicts"] == 1
    assert job["report"]["attempted_calls"] == 1
    fake.aclose.assert_awaited_once()
    shown = await cli.execute(cli.build_parser().parse_args(["show", job["id"]]), settings)
    assert shown["summary"]["item"]["status"] == "done"
    findings = await cli.execute(
        cli.build_parser().parse_args(["list", "--kind", "conflicts", "--outcome", "conflict"]), settings
    )
    finding = findings["summary"]["items"][0]
    assert finding["review_status"] == "unreviewed"
    detail = await cli.execute(
        cli.build_parser().parse_args(["show", "--kind", "conflicts", finding["id"]]), settings
    )
    saved = detail["summary"]["item"]
    answers = {"a": "До 10 МБ.", "b": "До 20 МБ."}
    for side in ("left", "right"):
        assert saved["assessment"][f"{side}_quote"] == answers[saved[side]["article_id"]]
    resumed = await cli.execute(cli.build_parser().parse_args(["resume", job["id"]]), settings)
    assert resumed["status"] == "completed"
    assert fake.assess.await_count == 1


def test_main_success_uses_explicit_database_override(monkeypatch, capsys):
    captured = []

    async def success(args, settings):
        captured.append(settings)
        return cli._envelope("completed", {"items": []}, [])

    monkeypatch.setattr(cli, "execute", success)
    assert cli.main(["--database-url", "sqlite+aiosqlite:///isolated.sqlite3", "list"]) == 0
    assert captured[0].database_url.endswith("isolated.sqlite3")
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
