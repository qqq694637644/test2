"""Guest-side client agent for disposable VM smoke jobs.

The agent is intentionally a client: it never listens on a guest port and it
only talks to the configured host controller endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

GUEST_DEPENDENCY_HINT = (
    "The guest VM snapshot must have guest dependencies installed before the "
    "snapshot is taken. Check them with: "
    "python -m vm_test_template.guest_vm.required_imports. "
    "Install them with: python -m pip install -r "
    "src/vm_test_template/guest_vm/requirements.txt "
    "--proxy http://192.168.1.249:10810"
)
DEFAULT_AGENT_VERSION = "0.1.0"


def _require_requests() -> Any:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - depends on optional extra.
        raise RuntimeError(GUEST_DEPENDENCY_HINT) from exc
    return requests


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class AgentConfig:
    controller_url: str
    guest_id: str = "vm-test-guest"
    agent_version: str = DEFAULT_AGENT_VERSION
    boot_id: str = ""
    work_root: Path = Path(tempfile.gettempdir()) / "vm-test-template-guest"
    connect_retries: int = 60
    connect_retry_delay: float = 5.0
    request_timeout: float = 10.0
    result_tail_bytes: int = 32768


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def _coerce_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def tail_text(value: str | bytes | None, max_bytes: int) -> str:
    """Return a UTF-8-safe tail of a potentially large text value."""

    text = _coerce_text(value)
    data = text.encode("utf-8", errors="replace")
    if len(data) <= max_bytes:
        return text
    return data[-max_bytes:].decode("utf-8", errors="replace")


def normalize_controller_url(value: str) -> str:
    """Return a requests-compatible controller URL.

    Operators often type a reachable endpoint as ``host:port``. ``requests``
    needs an explicit scheme, so treat a missing scheme as ``http://``.
    """

    url = value.strip().rstrip("/")
    if not url:
        raise ValueError("controller_url must not be empty")
    if "://" not in url:
        url = f"http://{url}"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"controller_url must be an HTTP URL, got {value!r}")
    return url


def normalize_command(value: Any) -> list[str]:
    """Validate command payloads without invoking a shell."""

    if not isinstance(value, list) or not value:
        raise ValueError("command must be a non-empty list of strings")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError("command entries must be non-empty strings")
    return list(value)


def build_hello(config: AgentConfig) -> dict[str, Any]:
    return {
        "guest_id": config.guest_id,
        "hostname": socket.gethostname(),
        "agent_version": config.agent_version,
        "boot_id": config.boot_id or uuid.uuid4().hex,
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
    }


def _post_json(session: Any, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    response = session.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    if not response.content:
        return {}
    return dict(response.json())


def _get_json(session: Any, url: str, timeout: float) -> dict[str, Any]:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return dict(response.json())


def _upload_log(
    session: Any,
    controller_url: str,
    job_id: str,
    message: str,
    *,
    stream: str = "agent",
    timeout: float,
) -> None:
    payload = {
        "job_id": job_id,
        "stream": stream,
        "message": message,
        "timestamp": utc_now_iso(),
    }
    try:
        _post_json(session, f"{controller_url}/log/{job_id}", payload, timeout)
    except Exception as exc:  # noqa: BLE001 - logging must not hide the real result.
        print(f"WARN: failed to upload guest log: {type(exc).__name__}: {exc}", file=sys.stderr)


def _command_env(payload: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    payload_env = payload.get("env") or {}
    if not isinstance(payload_env, dict):
        raise ValueError("payload env must be an object")
    for key, value in payload_env.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("payload env keys and values must be strings")
        env[key] = value
    return env


def _payload_cwd(payload: dict[str, Any], job_dir: Path) -> Path:
    cwd = payload.get("cwd")
    if cwd is None:
        return job_dir
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("payload cwd must be a non-empty string")
    return Path(cwd)


def _completed_result(
    *,
    job_id: str,
    status: str,
    exit_code: int,
    stdout: str | bytes | None,
    stderr: str | bytes | None,
    duration_seconds: float,
    tail_bytes: int,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "status": status,
        "exit_code": int(exit_code),
        "stdout_tail": tail_text(stdout, tail_bytes),
        "stderr_tail": tail_text(stderr, tail_bytes),
        "artifacts": [],
        "duration_seconds": duration_seconds,
        "metadata": metadata,
    }


def execute_payload(
    payload: dict[str, Any],
    job_dir: Path,
    *,
    default_timeout_seconds: int,
    tail_bytes: int,
) -> dict[str, Any]:
    """Execute a downloaded task payload and return a result JSON object."""

    started = time.monotonic()
    job_id = str(payload.get("job_id") or "")
    action = payload.get("action")
    timeout_seconds = int(payload.get("timeout_seconds") or default_timeout_seconds)
    metadata: dict[str, Any] = {
        "action": action,
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "work_dir": str(job_dir),
    }

    try:
        job_dir.mkdir(parents=True, exist_ok=True)
        if action == "noop":
            stdout = (
                "noop completed\n"
                f"python_version={platform.python_version()}\n"
                f"python_executable={sys.executable}\n"
            )
            return _completed_result(
                job_id=job_id,
                status="completed",
                exit_code=0,
                stdout=stdout,
                stderr="",
                duration_seconds=time.monotonic() - started,
                tail_bytes=tail_bytes,
                metadata=metadata,
            )

        if action == "command":
            command = normalize_command(payload.get("command"))
            metadata["command"] = command
            process = subprocess.run(
                command,
                cwd=str(_payload_cwd(payload, job_dir)),
                env=_command_env(payload),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
            return _completed_result(
                job_id=job_id,
                status="completed",
                exit_code=process.returncode,
                stdout=process.stdout,
                stderr=process.stderr,
                duration_seconds=time.monotonic() - started,
                tail_bytes=tail_bytes,
                metadata=metadata,
            )

        if action == "python_script":
            script_text = payload.get("script_text")
            if not isinstance(script_text, str) or not script_text:
                raise ValueError("python_script payload requires script_text")
            script_path = job_dir / "payload.py"
            script_path.write_text(script_text, encoding="utf-8")
            command = [sys.executable, str(script_path)]
            metadata["command"] = command
            process = subprocess.run(
                command,
                cwd=str(job_dir),
                env=_command_env(payload),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
            return _completed_result(
                job_id=job_id,
                status="completed",
                exit_code=process.returncode,
                stdout=process.stdout,
                stderr=process.stderr,
                duration_seconds=time.monotonic() - started,
                tail_bytes=tail_bytes,
                metadata=metadata,
            )

        raise ValueError(f"unsupported task action: {action!r}")
    except subprocess.TimeoutExpired as exc:
        metadata["timeout_seconds"] = timeout_seconds
        stderr = _coerce_text(exc.stderr) + f"\nTimed out after {timeout_seconds} seconds."
        return _completed_result(
            job_id=job_id,
            status="timeout",
            exit_code=124,
            stdout=exc.stdout,
            stderr=stderr,
            duration_seconds=time.monotonic() - started,
            tail_bytes=tail_bytes,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001 - malformed payloads must be reported to host.
        metadata["error_type"] = type(exc).__name__
        return _completed_result(
            job_id=job_id,
            status="error",
            exit_code=1,
            stdout="",
            stderr=traceback.format_exc(),
            duration_seconds=time.monotonic() - started,
            tail_bytes=tail_bytes,
            metadata=metadata,
        )


def _hello_with_retry(session: Any, config: AgentConfig, hello: dict[str, Any]) -> dict[str, Any]:
    controller_url = normalize_controller_url(config.controller_url)
    last_error: BaseException | None = None
    for attempt in range(1, config.connect_retries + 1):
        try:
            return _post_json(
                session,
                f"{controller_url}/hello",
                hello,
                config.request_timeout,
            )
        except Exception as exc:  # noqa: BLE001 - retry connection bootstrap.
            last_error = exc
            print(
                f"WARN: /hello attempt {attempt}/{config.connect_retries} failed: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            if attempt < config.connect_retries:
                time.sleep(config.connect_retry_delay)
    assert last_error is not None
    raise last_error


def run_once(config: AgentConfig, session: Any | None = None) -> int:
    requests = _require_requests()
    owned_session = session is None
    if session is None:
        session = requests.Session()

    controller_url = normalize_controller_url(config.controller_url)
    try:
        hello = build_hello(config)
        hello_response = _hello_with_retry(session, config, hello)
        job_id = str(hello_response["job_id"])
        payload_url = str(hello_response["payload_url"])
        timeout_seconds = int(hello_response["timeout_seconds"])
        job_dir = config.work_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(job_dir / "hello_response.json", hello_response)
        _upload_log(
            session,
            controller_url,
            job_id,
            f"guest connected; python={platform.python_version()}; work_dir={job_dir}",
            timeout=config.request_timeout,
        )

        try:
            payload = _get_json(session, payload_url, config.request_timeout)
            _atomic_write_json(job_dir / "payload.json", payload)
            result = execute_payload(
                payload,
                job_dir,
                default_timeout_seconds=timeout_seconds,
                tail_bytes=config.result_tail_bytes,
            )
        except Exception as exc:  # noqa: BLE001 - report payload download/execution errors.
            result = _completed_result(
                job_id=job_id,
                status="error",
                exit_code=1,
                stdout="",
                stderr=traceback.format_exc(),
                duration_seconds=0,
                tail_bytes=config.result_tail_bytes,
                metadata={"error_type": type(exc).__name__},
            )

        _atomic_write_json(job_dir / "result.json", result)
        _upload_log(
            session,
            controller_url,
            job_id,
            f"guest finished; status={result['status']}; exit_code={result['exit_code']}",
            timeout=config.request_timeout,
        )
        _post_json(session, f"{controller_url}/result/{job_id}", result, config.request_timeout)
        return int(result.get("exit_code") or 0)
    finally:
        if owned_session:
            session.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-url", default=os.environ.get("VM_TEST_TEMPLATE_CONTROLLER_URL"))
    parser.add_argument(
        "--guest-id",
        default=os.environ.get("VM_TEST_TEMPLATE_GUEST_ID", "vm-test-guest"),
    )
    parser.add_argument("--agent-version", default=DEFAULT_AGENT_VERSION)
    parser.add_argument("--boot-id", default=os.environ.get("VM_TEST_TEMPLATE_GUEST_BOOT_ID", ""))
    parser.add_argument(
        "--work-root",
        default=os.environ.get(
            "VM_TEST_TEMPLATE_GUEST_WORK_ROOT",
            str(Path(tempfile.gettempdir()) / "vm-test-template-guest"),
        ),
    )
    parser.add_argument("--connect-retries", type=int, default=60)
    parser.add_argument("--connect-retry-delay", type=float, default=5.0)
    parser.add_argument("--request-timeout", type=float, default=10.0)
    parser.add_argument("--result-tail-bytes", type=int, default=32768)
    args = parser.parse_args(argv)
    if not args.controller_url:
        parser.error("--controller-url or VM_TEST_TEMPLATE_CONTROLLER_URL is required")
    return args


def config_from_args(args: argparse.Namespace) -> AgentConfig:
    return AgentConfig(
        controller_url=normalize_controller_url(str(args.controller_url)),
        guest_id=args.guest_id,
        agent_version=args.agent_version,
        boot_id=args.boot_id,
        work_root=Path(args.work_root),
        connect_retries=args.connect_retries,
        connect_retry_delay=args.connect_retry_delay,
        request_timeout=args.request_timeout,
        result_tail_bytes=args.result_tail_bytes,
    )


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    raise SystemExit(run_once(config_from_args(args)))


if __name__ == "__main__":  # pragma: no cover
    main()

