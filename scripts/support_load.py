"""Small reproducible local load check for the support hand-off endpoint."""
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8099"


async def one(client: httpx.AsyncClient, number: int) -> dict:
    created = (await client.post(f"{BASE}/api/chat/sessions", json={"audience": "supplier"})).json()
    started = time.perf_counter()
    response = await client.post(
        f"{BASE}/api/chat/sessions/{created['session_id']}/messages",
        headers={"X-Session-Owner": created["owner"]},
        json={
            "text": f"Нужен оператор, как зарегистрироваться? Нагрузочный кейс {number}",
            "request_id": f"load-{number}",
        },
    )
    return {"status": response.status_code, "latency_ms": (time.perf_counter() - started) * 1000}


async def main() -> None:
    output = {}
    async with httpx.AsyncClient(timeout=30) as client:
        offset = 0
        for concurrency in (1, 3, 10):
            rows = await asyncio.gather(*(one(client, offset + i) for i in range(concurrency)))
            offset += concurrency
            timings = sorted(row["latency_ms"] for row in rows)
            output[str(concurrency)] = {
                "requests": len(rows),
                "errors": sum(row["status"] != 200 for row in rows),
                "p50_ms": statistics.median(timings),
                "p95_ms": timings[max(0, int(0.95 * len(timings)) - 1)],
            }
    target = Path(__file__).parents[1] / "reports/support-load.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
