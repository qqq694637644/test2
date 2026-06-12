"""GitHub Actions Python entrypoint for disposable VM template workflows.

The workflow should call this file with ``shell: python``. It reads
``workflow_dispatch.inputs`` from GitHub's event JSON file, writes any selected
payload script as a reviewable file, and starts the host controller.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:  # Support direct file execution from GitHub Actions.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vm_test_template.disposable_vm.host_controller import parse_args, run_controller

DEFAULT_CONTROLLER_URL = "http://192.168.1.249:8766"
DEFAULT_RESTORE_SCRIPT = r"C:\Users\alion\Scripts\vmware_restore_test1.py"
DEFAULT_COMMAND_JSON = '["python", "--version"]'
DEFAULT_PYTHON_SCRIPT_TEXT = "\n".join(
    [
        "import platform",
        "import sys",
        "print('vm-test-template python_script ok')",
        "print('python=' + platform.python_version())",
        "print('executable=' + sys.executable)",
        "",
    ]
)
RESULT_DIR_NAME = "vm-test-template-disposable-vm"


@dataclass(frozen=True)
class WorkflowInputs:
    controller_url: str
    port: int
    restore_script: str
    restore_gui: bool
    run_vmware_restore: bool
    restore_extra_args: tuple[str, ...]
    task_action: str
    command_json: str
    python_script_text: str
    connect_timeout_seconds: int
    run_timeout_seconds: int
    result_dir: Path


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _read_github_event_inputs() -> dict[str, object]:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return {}
    path = Path(event_path)
    if not path.is_file():
        return {}
    try:
        event = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Cannot read GitHub event JSON from {path}: {exc}") from exc
    inputs = event.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def _input_value(inputs: dict[str, object], key: str, default: str = "") -> str:
    value = inputs.get(key)
    if value is None:
        # Local-test/debug fallback. The workflow itself reads from GITHUB_EVENT_PATH.
        value = os.environ.get(f"VM_TEST_TEMPLATE_{key.upper()}")
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return default
    text = str(value)
    return text if text.strip() else default


def _parse_bool(inputs: dict[str, object], name: str, default: bool) -> bool:
    raw = _input_value(inputs, name, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"workflow input {name!r} must be boolean-like, got {raw!r}")


def _parse_int(inputs: dict[str, object], name: str, default: int) -> int:
    raw = _input_value(inputs, name, str(default)).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"workflow input {name!r} must be an integer, got {raw!r}") from exc


def _parse_json_string_list(
    inputs: dict[str, object],
    name: str,
    default: str = "[]",
) -> tuple[str, ...]:
    raw = _input_value(inputs, name, default)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"workflow input {name!r} must be a JSON array of strings: {raw!r}") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError(f"workflow input {name!r} must be a JSON array of strings: {raw!r}")
    return tuple(item for item in parsed if item.strip())


def _default_result_dir() -> Path:
    runner_temp = os.environ.get("RUNNER_TEMP")
    root = Path(runner_temp) if runner_temp else Path(tempfile.gettempdir())
    return root / RESULT_DIR_NAME


def inputs_from_env() -> WorkflowInputs:
    inputs = _read_github_event_inputs()
    port = _parse_int(inputs, "port", 8766)
    controller_url = _input_value(inputs, "controller_url", DEFAULT_CONTROLLER_URL)
    if not controller_url.strip():
        controller_url = f"http://192.168.1.249:{port}"

    result_dir_raw = _input_value(inputs, "result_dir", "")
    result_dir = Path(result_dir_raw) if result_dir_raw else _default_result_dir()

    return WorkflowInputs(
        controller_url=controller_url.rstrip("/"),
        port=port,
        restore_script=_input_value(inputs, "restore_script", DEFAULT_RESTORE_SCRIPT),
        restore_gui=_parse_bool(inputs, "restore_gui", True),
        run_vmware_restore=_parse_bool(inputs, "run_vmware_restore", True),
        restore_extra_args=_parse_json_string_list(inputs, "restore_extra_args_json"),
        task_action=_input_value(inputs, "task_action", "noop"),
        command_json=_input_value(inputs, "command_json", DEFAULT_COMMAND_JSON),
        python_script_text=_input_value(
            inputs,
            "python_script_text",
            DEFAULT_PYTHON_SCRIPT_TEXT,
        ),
        connect_timeout_seconds=_parse_int(inputs, "connect_timeout_seconds", 600),
        run_timeout_seconds=_parse_int(inputs, "run_timeout_seconds", 1800),
        result_dir=result_dir,
    )


def _write_script(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"Wrote workflow payload script: {path}", flush=True)
    return path


def _controller_args(inputs: WorkflowInputs) -> list[str]:
    args = [
        "--bind-host",
        "0.0.0.0",
        "--port",
        str(inputs.port),
        "--advertise-url",
        inputs.controller_url,
        "--task-action",
        inputs.task_action,
        "--connect-timeout-seconds",
        str(inputs.connect_timeout_seconds),
        "--timeout-seconds",
        str(inputs.run_timeout_seconds),
        "--result-dir",
        str(inputs.result_dir),
    ]

    if inputs.run_vmware_restore:
        args.extend(["--vmware-restore-script", inputs.restore_script])
        if inputs.restore_gui:
            args.append("--vmware-restore-arg=--gui")
        for restore_arg in inputs.restore_extra_args:
            args.append(f"--vmware-restore-arg={restore_arg}")
    else:
        print("Skipping VMware restore; waiting for an already-running guest agent.", flush=True)

    if inputs.task_action == "command":
        args.extend(["--command-json", inputs.command_json])
        return args

    if inputs.task_action == "python_script":
        script_path = _write_script(inputs.result_dir / "workflow_payload.py", inputs.python_script_text)
        args.extend(["--script-path", str(script_path)])
        return args

    return args


def main() -> int:
    _configure_stdio()
    inputs = inputs_from_env()
    inputs.result_dir.mkdir(parents=True, exist_ok=True)
    print(f"Using controller_url={inputs.controller_url}", flush=True)
    print(f"Using result_dir={inputs.result_dir}", flush=True)
    args = _controller_args(inputs)
    print("Starting disposable VM host controller from Python workflow runner.", flush=True)
    parsed = parse_args(args)
    return int(run_controller(parsed))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
