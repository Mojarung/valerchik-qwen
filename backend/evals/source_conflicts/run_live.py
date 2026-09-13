"""Explicit live-model eval. Run from backend; uses configured DB-backed LLM settings."""

import argparse
import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import Database
from app.schemas.source_conflicts import SourceSnapshot
from app.services.source_conflict_detector import create_conflict_detector
from app.tools.source_conflicts import _load_llm_config

MAX_CASES = 20


def source(case: dict[str, Any], side: str) -> SourceSnapshot:
    text = str(case[side])
    return SourceSnapshot(
        id=f"{case['id']}-{side}",
        article_id=f"{case['id']}-{side}",
        source_version="eval-v1",
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        question=str(case.get(f"{side}_question", case["question"])),
        answer=text,
        audience=case.get(f"{side}_audience", "supplier"),
        kind="answer",
        category="Проверка качества",
        source="synthetic",
        doc_key="",
        origin={},
        created_at=datetime.now(UTC),
    )


async def run(cases_path: Path, output: Path) -> None:
    cases = json.loads(await asyncio.to_thread(cases_path.read_text))
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise ValueError("Provide 1..20 explicit cases")
    settings = Settings()
    db = Database(settings.effective_database_url)
    detector = create_conflict_detector(await _load_llm_config(db.session_factory, settings))
    report: dict[str, Any] = {"detector_version": detector.version, "cases": []}
    try:
        for case in cases:
            started = time.perf_counter()
            result: dict[str, Any] = {"id": case["id"], "expected": case["expected"]}
            try:
                assessment = await detector.assess(
                    source(case, "left"), source(case, "right"), timeout_s=60, max_output_tokens=2500
                )
                result.update(
                    outcome=assessment.outcome,
                    passed=assessment.outcome == case["expected"],
                    assessment=assessment.model_dump(),
                )
            except Exception as exc:  # noqa: BLE001 — save safe per-case failure, continue bounded suite
                result.update(error=type(exc).__name__, error_message=str(exc), passed=False)
            result["seconds"] = round(time.perf_counter() - started, 2)
            report["cases"].append(result)
            output.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(
                output.write_text, json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            print(
                json.dumps({key: value for key, value in result.items() if key != "assessment"}), flush=True
            )
    finally:
        await detector.aclose()
        await db.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("evals/source_conflicts/cases.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    asyncio.run(run(args.cases, args.output))
