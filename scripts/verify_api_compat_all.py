import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [
    "verify_api_compat_openai.py",
    "verify_api_compat_anthropic_messages.py",
    "verify_api_compat_gemini_generate_content.py",
]


def _run_script(script_name: str) -> dict:
    script_path = ROOT / "scripts" / script_name
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) if not existing_pythonpath else f"{ROOT}{os.pathsep}{existing_pythonpath}"
    env.setdefault("ADMIN_PASSWORD", "test-admin-password")

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )

    stdout_lines = [line for line in result.stdout.splitlines() if line.strip()]
    payload = None
    if stdout_lines:
        try:
            payload = json.loads(stdout_lines[-1])
        except json.JSONDecodeError:
            payload = {"raw_stdout": result.stdout}

    return {
        "script": script_name,
        "exit_code": int(result.returncode),
        "ok": result.returncode == 0,
        "payload": payload,
        "stderr": result.stderr.strip(),
    }


def main() -> None:
    results = [_run_script(script_name) for script_name in SCRIPTS]
    failed = [item for item in results if not item["ok"]]
    output = {
        "ok": not failed,
        "scripts": results,
        "failed_scripts": [item["script"] for item in failed],
    }
    print(json.dumps(output, ensure_ascii=False))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
