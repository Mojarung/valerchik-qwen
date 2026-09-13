"""Run independent test modules with bounded time and persist every result."""
import asyncio
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
output = root / "reports/support-tests"
output.mkdir(parents=True, exist_ok=True)


async def main():
    limit = asyncio.Semaphore(4)
    async def run(path):
        async with limit:
            process = await asyncio.create_subprocess_exec(sys.executable, "-m", "pytest", "-q", str(path),
                "--disable-warnings", "--tb=short", cwd=root / "backend",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            try:
                data, _ = await asyncio.wait_for(process.communicate(), timeout=90)
                status = process.returncode
            except TimeoutError:
                process.kill()
                data, _ = await process.communicate()
                status = "timeout"
            text = data.decode(errors="replace")
            (output / f"{path.stem}.log").write_text(text)
            result = {"module": path.name, "status": status, "summary": text.splitlines()[-1:]}
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return result
    results = await asyncio.gather(*(run(path) for path in sorted((root / "backend/tests").glob("test_*.py"))))
    (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))


asyncio.run(main())
