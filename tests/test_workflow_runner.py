"""Tests for the Python-only disposable VM workflow runner."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vm_test_template.disposable_vm.workflow_runner import (  # noqa: E402
    DEFAULT_COMMAND_JSON,
    WorkflowInputs,
    _controller_args,
    inputs_from_env,
)
from vm_test_template.payload.disposable_vm import TaskAction, TaskPayload  # noqa: E402


def _inputs(tmp_path: Path, task_action: str = "noop") -> WorkflowInputs:
    return WorkflowInputs(
        controller_url="http://192.168.1.249:8766",
        port=8766,
        restore_script=r"C:\Users\alion\Scripts\vmware_restore_test1.py",
        restore_gui=True,
        run_vmware_restore=True,
        restore_extra_args=(),
        task_action=task_action,
        command_json=DEFAULT_COMMAND_JSON,
        python_script_text="print('hello from template')\n",
        connect_timeout_seconds=600,
        run_timeout_seconds=1800,
        result_dir=tmp_path,
    )


def test_python_script_action_writes_reviewable_payload_file(tmp_path):
    args = _controller_args(_inputs(tmp_path, "python_script"))

    assert args[args.index("--task-action") + 1] == "python_script"
    script_path = Path(args[args.index("--script-path") + 1])
    assert script_path.is_file()
    assert script_path.name == "workflow_payload.py"
    assert "hello from template" in script_path.read_text(encoding="utf-8")


def test_command_action_passes_command_json(tmp_path):
    args = _controller_args(_inputs(tmp_path, "command"))

    assert args[args.index("--task-action") + 1] == "command"
    assert args[args.index("--command-json") + 1] == DEFAULT_COMMAND_JSON


def test_workflow_yaml_uses_python_files_not_powershell_or_cmd():
    workflow = Path(".github/workflows/disposable-vm-guest-agent-smoke.yml").read_text(encoding="utf-8")

    assert "shell: python" in workflow
    assert "shell: cmd" not in workflow
    assert "shell: pwsh" not in workflow
    assert "$ErrorActionPreference" not in workflow
    assert "runpy.run_path" in workflow
    assert "workflow_runner.py" in workflow
    assert "workflow_install.py" in workflow


def test_workflow_runner_reads_github_event_inputs(tmp_path, monkeypatch):
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "inputs": {
                    "controller_url": "http://example.invalid:9000",
                    "port": "9000",
                    "restore_script": r"C:\restore.py",
                    "restore_gui": "false",
                    "run_vmware_restore": "false",
                    "restore_extra_args_json": '["--snapshot", "test"]',
                    "task_action": "command",
                    "command_json": '["python", "-V"]',
                    "python_script_text": "print('ignored for command')",
                    "connect_timeout_seconds": "12",
                    "run_timeout_seconds": "13",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))

    parsed = inputs_from_env()

    assert parsed.controller_url == "http://example.invalid:9000"
    assert parsed.port == 9000
    assert parsed.restore_gui is False
    assert parsed.run_vmware_restore is False
    assert parsed.restore_extra_args == ("--snapshot", "test")
    assert parsed.task_action == "command"
    assert parsed.connect_timeout_seconds == 12
    assert parsed.run_timeout_seconds == 13
    assert parsed.result_dir == tmp_path / "vm-test-template-disposable-vm"


def test_payload_model_validates_generic_actions():
    payload = TaskPayload(job_id="job-1", action=TaskAction.NOOP, timeout_seconds=10)
    assert payload.action is TaskAction.NOOP

    command = TaskPayload(
        job_id="job-2",
        action=TaskAction.COMMAND,
        timeout_seconds=10,
        command=["python", "--version"],
    )
    assert command.command == ["python", "--version"]

    script = TaskPayload(
        job_id="job-3",
        action=TaskAction.PYTHON_SCRIPT,
        timeout_seconds=10,
        script_text="print('ok')",
    )
    assert script.script_text == "print('ok')"
