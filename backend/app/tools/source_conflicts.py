"""Manual source-conflict audit in a dedicated process; never starts the app runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.adapters.embeddings import create_embedder
from app.config import Settings
from app.db import Database, SessionFactory
from app.dynamic_settings import SETTINGS
from app.models import SettingEntry
from app.schemas.source_conflicts import AuditLimits

_LIMIT_NAMES = (
    "max_articles",
    "max_pairs",
    "top_k",
    "max_article_chars",
    "timeout_seconds",
    "request_timeout_seconds",
    "max_calls",
    "max_output_tokens",
)
_MAX_LIST_LIMIT = 200
_MIN_ARTICLES = 2


class CliError(Exception):
    """Safe, fixed diagnostic code suitable for machine output."""


@dataclass
class _ReadDatabase:
    engine: AsyncEngine
    session_factory: SessionFactory

    async def dispose(self) -> None:
        await self.engine.dispose()


@dataclass
class _LLMConfig:
    values: dict[str, Any]

    def get(self, key: str) -> Any:
        return self.values.get(key)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="Override DATABASE_URL for this invocation")
    parser.add_argument("--data-dir", type=Path, help="Override DATA_DIR for a separate local database")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-schema", help="Explicitly initialize/update database schema, without seeding")
    audit = commands.add_parser("audit", help="Audit only explicitly selected article IDs")
    audit.add_argument("--article-id", action="append", required=True, dest="article_ids")
    audit.add_argument("--actor", default="cli")
    defaults = AuditLimits()
    for name in _LIMIT_NAMES:
        default = getattr(defaults, name)
        audit.add_argument(
            "--" + name.replace("_", "-"), type=float if name.endswith("seconds") else int, default=default
        )
    resume = commands.add_parser("resume", help="Resume a saved job with its original scope and limits")
    resume.add_argument("id")
    listing = commands.add_parser("list", help="Read saved jobs or findings without model initialization")
    listing.add_argument("--kind", choices=("jobs", "conflicts"), default="jobs")
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--offset", type=int, default=0)
    listing.add_argument("--article-id")
    listing.add_argument("--outcome", choices=("conflict", "compatible", "uncertain"))
    listing.add_argument("--include-stale", action="store_true")
    showing = commands.add_parser("show", help="Read one saved job or pair result")
    showing.add_argument("id")
    showing.add_argument("--kind", choices=("jobs", "conflicts"), default="jobs")
    return parser


def _validate(args: argparse.Namespace) -> AuditLimits | None:
    if args.command == "list":
        if not 1 <= args.limit <= _MAX_LIST_LIMIT or args.offset < 0:
            raise CliError("invalid_pagination")
        if args.kind == "jobs" and (args.article_id or args.outcome or args.include_stale):
            raise CliError("filters_require_conflicts")
    if args.command != "audit":
        return None
    try:
        limits = AuditLimits(**{name: getattr(args, name) for name in _LIMIT_NAMES})
    except ValidationError as exc:
        raise CliError("invalid_audit_limits") from exc
    if (
        len(args.article_ids) < _MIN_ARTICLES
        or len(args.article_ids) > limits.max_articles
        or len(set(args.article_ids)) != len(args.article_ids)
        or any(not value.strip() for value in args.article_ids)
    ):
        raise CliError("invalid_article_scope")
    return limits


def _open_database(settings: Settings, *, read_only: bool) -> Database | _ReadDatabase:
    # Do not call effective_database_url for reads: it creates data_dir.
    url = settings.database_url or f"sqlite+aiosqlite:///{settings.data_dir / 'support.sqlite3'}"
    if not read_only:
        if not settings.database_url:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
        return Database(url)
    parsed = make_url(url)
    if parsed.drivername.startswith("sqlite"):
        filename = parsed.database or ""
        if filename == ":memory:" or not filename:
            raise CliError("readonly_requires_existing_database")
        if filename.startswith("file:"):
            database = filename
        else:
            database = "file:" + quote(str(Path(filename).resolve()), safe="/")
        parsed = parsed.set(database=database, query={**parsed.query, "mode": "ro", "uri": "true"})
    engine = create_async_engine(parsed, echo=False)
    return _ReadDatabase(engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False))


async def _load_llm_config(session_factory: SessionFactory, settings: Settings) -> _LLMConfig:
    values = {
        "llm.base_url": settings.llm_base_url or SETTINGS["llm.base_url"].default,
        "llm.api_key": settings.llm_api_key or SETTINGS["llm.api_key"].default,
        "llm.model": settings.llm_model or SETTINGS["llm.model"].default,
    }
    async with session_factory() as session:
        rows = (await session.execute(select(SettingEntry).where(SettingEntry.key.in_(values)))).scalars()
        for row in rows:
            values[row.key] = row.value.get("v", SETTINGS[row.key].default)
    return _LLMConfig(values)


def _envelope(status: str, summary: dict[str, Any], next_actions: list[str]) -> dict[str, Any]:
    return {"status": status, "summary": summary, "next_actions": next_actions, "artifacts": []}


async def _read_command(args: argparse.Namespace, registry: Any) -> dict[str, Any]:
    if args.command == "show":
        getter = registry.get_job if args.kind == "jobs" else registry.get_result
        item = await getter(args.id)
        if item is None:
            raise CliError("not_found")
        return _envelope("completed", {"kind": args.kind, "item": item.model_dump(mode="json")}, [])
    if args.kind == "jobs":
        items = await registry.list_jobs(limit=args.limit, offset=args.offset)
    else:
        items = await registry.list_results(
            article_id=args.article_id,
            outcome=args.outcome,
            reviewed=None,
            include_stale=args.include_stale,
            limit=args.limit,
            offset=args.offset,
        )
    return _envelope(
        "completed",
        {
            "kind": args.kind,
            "items": [item.model_dump(mode="json") for item in items],
            "limit": args.limit,
            "offset": args.offset,
        },
        [],
    )


async def _run_audit(
    args: argparse.Namespace,
    settings: Settings,
    registry: Any,
    session_factory: SessionFactory,
    limits: AuditLimits | None,
) -> dict[str, Any]:
    resume_id = None
    if args.command == "resume":
        job = await registry.get_job(args.id)
        if job is None:
            raise CliError("not_found")
        article_ids, limits, actor, resume_id = job.scope["article_ids"], job.limits, job.actor, job.id
    else:
        article_ids, actor = args.article_ids, args.actor
    config = await _load_llm_config(session_factory, settings)
    if not config.get("llm.base_url") or not config.get("llm.model"):
        raise CliError("llm_not_configured")
    from app.services.source_conflict_audit import SourceConflictAuditor
    from app.services.source_conflict_candidates import SourceConflictCandidateFinder
    from app.services.source_conflict_detector import create_conflict_detector

    detector = create_conflict_detector(config)
    embedder = None
    try:
        embedder = create_embedder(settings.tei_url)
        auditor = SourceConflictAuditor(registry, SourceConflictCandidateFinder(embedder), detector)
        assert limits is not None
        job = await auditor.run(article_ids, limits, resume_job_id=resume_id, actor=actor)
    finally:
        try:
            if embedder is not None and (close := getattr(embedder, "aclose", None)) is not None:
                await close()
        finally:
            await detector.aclose()
    next_actions = ["Review saved pair findings and source quotations before accepting any contradiction."]
    if job.status != "done":
        next_actions.append("Inspect stop_reason and limits with show before explicitly resuming this job.")
    return _envelope(
        "completed" if job.status == "done" else job.status,
        {"job": job.model_dump(mode="json")},
        next_actions,
    )


async def execute(args: argparse.Namespace, settings: Settings) -> dict[str, Any]:
    from app.services.source_conflicts import SourceConflictRegistry

    limits = _validate(args)
    database = _open_database(settings, read_only=args.command in ("list", "show"))
    try:
        if args.command == "init-schema":
            assert isinstance(database, Database)
            await database.create_all()
            return _envelope(
                "completed",
                {"schema_initialized": True},
                ["Select existing article IDs and run a bounded audit with a configured LLM."],
            )
        registry = SourceConflictRegistry(database.session_factory)
        if args.command in ("list", "show"):
            return await _read_command(args, registry)
        return await _run_audit(args, settings, registry, database.session_factory, limits)
    finally:
        await database.dispose()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Provider/driver messages may contain credential-bearing URLs or request content.
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        try:
            settings = Settings()
            if args.database_url is not None:
                settings.database_url = args.database_url
            if args.data_dir is not None:
                settings.data_dir = args.data_dir
            result = asyncio.run(execute(args, settings))
        except Exception as exc:  # noqa: BLE001 — fixed safe machine error, never raw provider errors
            code = str(exc) if isinstance(exc, CliError) else "operation_failed"
            result = _envelope(
                "failed",
                {"error": code},
                ["Check database/schema and LLM configuration; inspect saved jobs with list/show."],
            )
    finally:
        logging.disable(previous_logging)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
