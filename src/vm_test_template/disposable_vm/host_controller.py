"""One-shot host controller for disposable VM guest-agent smoke jobs."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vm_test_template.payload.disposable_vm import (
    GuestHello,
    GuestLog,
    GuestResult,
    HelloResponse,
    TaskAction,
    TaskPayload,
    utc_now_iso,
)

HOST_DEPENDENCY_HINT = (
    "Host controller dependencies are installed automatically by default. "
    "To preinstall them manually, run: "
    "py -3.11 -m pip install fastapi>=0.115.0 uvicorn>=0.30.0 "
    "--proxy http://192.168.1.249:10810"
)
PIP_PROXY = "http://192.168.1.249:10810"
HOST_RUNTIME_REQUIREMENTS = {
    "fastapi": "fastapi>=0.115.0",
    "uvicorn": "uvicorn>=0.30.0",
}


def _host_auto_install_enabled() -> bool:
    value = os.environ.get("VM_TEST_TEMPLATE_VM_HOST_AUTO_INSTALL", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _module_available(import_name: str) -> bool:
    return importlib.util.find_spec(import_name) is not None


def missing_host_runtime_modules(import_names: list[str] | None = None) -> list[str]:
    """Return host runtime import names that are currently unavailable."""

    names = import_names or list(HOST_RUNTIME_REQUIREMENTS)
    return [name for name in names if not _module_available(name)]


def _install_host_runtime_modules(import_names: list[str]) -> None:
    package_specs = [HOST_RUNTIME_REQUIREMENTS[name] for name in import_names]
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        *package_specs,
        "--proxy",
        PIP_PROXY,
    ]
    print(
        "Installing missing host controller dependencies: " + ", ".join(package_specs),
        flush=True,
    )
    subprocess.run(command, check=True)
    importlib.invalidate_caches()


def ensure_host_runtime_modules(import_names: list[str] | None = None) -> None:
    """Ensure host-only runtime dependencies are importable, installing if needed."""

    missing = missing_host_runtime_modules(import_names)
    if not missing:
        return
    if not _host_auto_install_enabled():
        package_specs = [HOST_RUNTIME_REQUIREMENTS[name] for name in missing]
        raise RuntimeError(
            "Missing host controller dependencies with auto-install disabled: "
            + ", ".join(package_specs)
            + ". "
            + HOST_DEPENDENCY_HINT
        )

    _install_host_runtime_modules(missing)
    still_missing = missing_host_runtime_modules(import_names)
    if still_missing:
        package_specs = [HOST_RUNTIME_REQUIREMENTS[name] for name in still_missing]
        raise RuntimeError(
            "Host controller dependency installation finished but imports still fail: "
            + ", ".join(package_specs)
        )


def _require_fastapi() -> tuple[Any, Any]:
    ensure_host_runtime_modules(["fastapi"])
    try:
        from fastapi import FastAPI, HTTPException
    except ImportError as exc:  # pragma: no cover - depends on optional extra.
        raise RuntimeError(HOST_DEPENDENCY_HINT) from exc
    return FastAPI, HTTPException


def _require_uvicorn() -> Any:
    ensure_host_runtime_modules(["uvicorn"])
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - depends on optional extra.
        raise RuntimeError(HOST_DEPENDENCY_HINT) from exc
    return uvicorn


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def _append_ndjson(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        output.write("\n")


@dataclass
class ControllerState:
    """Mutable one-shot controller state shared with FastAPI route handlers."""

    job_id: str
    advertise_url: str
    payload: TaskPayload
    result_dir: Path
    started_at: str = field(default_factory=utc_now_iso)
    status: str = "waiting_for_guest"
    hello: GuestHello | None = None
    result: GuestResult | None = None
    payload_downloaded: bool = False
    payload_downloaded_at: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    log_count: int = 0
    hello_event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    result_event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def payload_url(self) -> str:
        return f"{self.advertise_url.rstrip('/')}/payload/{self.job_id}"

    def hello_response(self) -> HelloResponse:
        return HelloResponse(
            job_id=self.job_id,
            action=self.payload.action,
            payload_url=self.payload_url,
            timeout_seconds=self.payload.timeout_seconds,
        )

    def mark_status(self, status: str, **metadata: Any) -> None:
        with self.lock:
            self.status = status
            event = {"timestamp": utc_now_iso(), "event": status}
            if metadata:
                event["metadata"] = metadata
            self.events.append(event)
            self._persist_locked()

    def record_hello(self, hello: GuestHello) -> None:
        with self.lock:
            self.hello = hello
            self.status = "guest_connected"
            self.events.append(
                {
                    "timestamp": utc_now_iso(),
                    "event": "guest_connected",
                    "guest_id": hello.guest_id,
                    "hostname": hello.hostname,
                    "python_version": hello.python_version,
                }
            )
            _atomic_write_json(
                self.result_dir / "hello.json",
                hello.model_dump(mode="json"),
            )
            self._persist_locked()
            self.hello_event.set()

    def record_payload_download(self) -> None:
        with self.lock:
            self.payload_downloaded = True
            self.payload_downloaded_at = utc_now_iso()
            self.status = "task_downloaded"
            self.events.append(
                {
                    "timestamp": self.payload_downloaded_at,
                    "event": "task_downloaded",
                    "action": self.payload.action.value,
                }
            )
            _atomic_write_json(
                self.result_dir / "payload.json",
                self.payload.model_dump(mode="json"),
            )
            self._persist_locked()

    def record_log(self, log: GuestLog) -> None:
        with self.lock:
            payload = log.model_dump(mode="json")
            if payload.get("timestamp") is None:
                payload["timestamp"] = utc_now_iso()
            self.log_count += 1
            _append_ndjson(self.result_dir / "guest_logs.ndjson", payload)
            self._persist_locked()

    def record_result(self, result: GuestResult) -> None:
        with self.lock:
            self.result = result
            if result.status == "completed" and result.exit_code == 0:
                self.status = "success"
            elif result.status == "completed" and result.exit_code != 0:
                self.status = "nonzero_exit"
            else:
                self.status = result.status
            self.events.append(
                {
                    "timestamp": utc_now_iso(),
                    "event": "result_received",
                    "status": result.status,
                    "exit_code": result.exit_code,
                }
            )
            _atomic_write_json(
                self.result_dir / "result.json",
                result.model_dump(mode="json"),
            )
            self._persist_locked()
            self.result_event.set()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return self._snapshot_locked()

    def persist(self) -> None:
        with self.lock:
            self._persist_locked()

    def _persist_locked(self) -> None:
        _atomic_write_json(self.result_dir / "controller_state.json", self._snapshot_locked())

    def _snapshot_locked(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "started_at": self.started_at,
            "advertise_url": self.advertise_url,
            "payload_url": self.payload_url,
            "payload_downloaded": self.payload_downloaded,
            "payload_downloaded_at": self.payload_downloaded_at,
            "hello": self.hello.model_dump(mode="json") if self.hello else None,
            "result": self.result.model_dump(mode="json") if self.result else None,
            "events": list(self.events),
            "log_count": self.log_count,
        }


def create_app(state: ControllerState) -> Any:
    """Create the FastAPI app for a single controller job."""

    fastapi_cls, http_exception_cls = _require_fastapi()
    app = fastapi_cls(title="VM Test Template Disposable VM Controller", version="0.1.0")

    def _validate_job_id(job_id: str) -> None:
        if job_id != state.job_id:
            raise http_exception_cls(status_code=404, detail="unknown job_id")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return state.snapshot()

    @app.post("/hello", response_model=HelloResponse)
    def hello(request: GuestHello) -> HelloResponse:
        state.record_hello(request)
        return state.hello_response()

    @app.get("/payload/{job_id}", response_model=TaskPayload)
    def payload(job_id: str) -> TaskPayload:
        _validate_job_id(job_id)
        state.record_payload_download()
        return state.payload

    @app.post("/log/{job_id}")
    def log(job_id: str, request: GuestLog) -> dict[str, bool]:
        _validate_job_id(job_id)
        if request.job_id != state.job_id:
            raise http_exception_cls(status_code=400, detail="body job_id does not match path")
        state.record_log(request)
        return {"ok": True}

    @app.post("/result/{job_id}")
    def result(job_id: str, request: GuestResult) -> dict[str, bool]:
        _validate_job_id(job_id)
        if request.job_id != state.job_id:
            raise http_exception_cls(status_code=400, detail="body job_id does not match path")
        state.record_result(request)
        return {"ok": True}

    return app


def _default_advertise_url(bind_host: str, port: int) -> str:
    host = "127.0.0.1" if bind_host in {"0.0.0.0", "::"} else bind_host
    return f"http://{host}:{port}"


def _parse_command_json(text: str | None) -> list[str]:
    if text is None:
        return ["python", "--version"]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--command-json is not valid JSON: {exc}") from exc
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("--command-json must be a JSON array of strings")
    return value


def build_payload(args: argparse.Namespace, job_id: str) -> TaskPayload:
    action = TaskAction(args.task_action)
    command: list[str] | None = None
    script_text: str | None = None

    if action is TaskAction.COMMAND:
        command = _parse_command_json(args.command_json)
    elif action is TaskAction.PYTHON_SCRIPT:
        if args.script_path is None:
            raise ValueError("--script-path is required for python_script tasks")
        script_text = Path(args.script_path).read_text(encoding="utf-8")

    return TaskPayload(
        job_id=job_id,
        action=action,
        timeout_seconds=args.timeout_seconds,
        command=command,
        script_text=script_text,
    )


def _start_server(
    app: Any,
    *,
    bind_host: str,
    port: int,
    log_level: str,
) -> tuple[Any, threading.Thread]:
    uvicorn = _require_uvicorn()
    config = uvicorn.Config(app, host=bind_host, port=port, log_level=log_level)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="disposable-vm-controller", daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not getattr(server, "started", False) and thread.is_alive():
        if time.monotonic() >= deadline:
            raise RuntimeError("timed out waiting for host controller server to start")
        time.sleep(0.1)
    if not thread.is_alive():
        raise RuntimeError("host controller server stopped during startup")
    return server, thread


def _stop_server(server: Any | None, thread: threading.Thread | None) -> None:
    if server is None or thread is None:
        return
    server.should_exit = True
    thread.join(timeout=10)


def _run_vmware_restore(args: argparse.Namespace, result_dir: Path) -> int | None:
    if args.vmware_restore_script is None:
        return None

    restore_script = Path(args.vmware_restore_script)
    if not restore_script.is_file():
        raise FileNotFoundError(f"VMware restore script does not exist: {restore_script}")

    env = os.environ.copy()
    cleared_tracking_id = env.pop("RUNNER_TRACKING_ID", None) is not None
    os.environ.pop("RUNNER_TRACKING_ID", None)

    command = [sys.executable, str(restore_script), *args.vmware_restore_arg]
    print("Running VMware restore command:", " ".join(command), flush=True)
    if cleared_tracking_id:
        print("Cleared RUNNER_TRACKING_ID before launching VMware restore.", flush=True)

    started_at = utc_now_iso()
    process = subprocess.run(
        command,
        cwd=str(restore_script.parent),
        env=env,
        check=False,
    )
    payload = {
        "command": command,
        "cwd": str(restore_script.parent),
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "returncode": process.returncode,
        "cleared_runner_tracking_id": cleared_tracking_id,
    }
    _atomic_write_json(result_dir / "vmware_restore.json", payload)
    return process.returncode


def run_controller(args: argparse.Namespace) -> int:
    job_id = args.job_id or f"vm-{uuid.uuid4().hex}"
    raw_advertise_url = args.advertise_url or _default_advertise_url(args.bind_host, args.port)
    advertise_url = raw_advertise_url.rstrip("/")
    result_dir = Path(args.result_dir or Path.cwd() / ".disposable-vm-results" / job_id)
    payload = build_payload(args, job_id)
    state = ControllerState(
        job_id=job_id,
        advertise_url=advertise_url,
        payload=payload,
        result_dir=result_dir,
    )
    state.persist()

    server: Any | None = None
    thread: threading.Thread | None = None
    try:
        app = create_app(state)
        server, thread = _start_server(
            app,
            bind_host=args.bind_host,
            port=args.port,
            log_level=args.log_level,
        )
        print(
            f"Host controller listening on {args.bind_host}:{args.port}; "
            f"advertising {advertise_url}; job_id={job_id}",
            flush=True,
        )

        restore_code = _run_vmware_restore(args, result_dir)
        if restore_code not in (None, 0):
            state.mark_status("controller_error", restore_returncode=restore_code)
            return restore_code if restore_code != 0 else 1

        if not state.hello_event.wait(args.connect_timeout_seconds):
            state.mark_status("guest_connect_timeout")
            return 2

        if not state.result_event.wait(args.timeout_seconds):
            if state.snapshot()["payload_downloaded"]:
                state.mark_status("run_timeout")
            else:
                state.mark_status("payload_download_timeout")
            return 3

        result = state.snapshot()["result"]
        if result is None:
            state.mark_status("result_missing")
            return 4
        if result["status"] != "completed" or result["exit_code"] != 0:
            exit_code = int(result.get("exit_code") or 1)
            return exit_code if exit_code != 0 else 1
        return 0
    except Exception as exc:
        state.mark_status("controller_error", error=f"{type(exc).__name__}: {exc}")
        _atomic_write_json(
            result_dir / "controller_error.json",
            {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        return 1
    finally:
        _stop_server(server, thread)
        state.persist()
        print(f"Controller result directory: {result_dir}", flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind-host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--advertise-url")
    parser.add_argument("--job-id")
    parser.add_argument("--result-dir")
    parser.add_argument(
        "--task-action",
        choices=[action.value for action in TaskAction],
        default=TaskAction.NOOP.value,
    )
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--connect-timeout-seconds", type=int, default=600)
    parser.add_argument(
        "--command-json",
        help='JSON command array for command tasks, for example: ["python", "--version"]',
    )
    parser.add_argument("--script-path", help="UTF-8 Python script used by python_script tasks")
    parser.add_argument("--vmware-restore-script")
    parser.add_argument(
        "--vmware-restore-arg",
        action="append",
        default=[],
        help="Argument passed to the VMware restore script; use --vmware-restore-arg=--gui",
    )
    parser.add_argument(
        "--no-auto-install-deps",
        action="store_true",
        help="Fail instead of pip-installing missing host controller dependencies.",
    )
    parser.add_argument("--log-level", default="info")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.no_auto_install_deps:
        os.environ["VM_TEST_TEMPLATE_VM_HOST_AUTO_INSTALL"] = "0"
    raise SystemExit(run_controller(args))


if __name__ == "__main__":  # pragma: no cover
    main()

