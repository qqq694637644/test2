"""Shared payload models for disposable VM host/guest execution.

The host controller and guest agent intentionally exchange a tiny JSON protocol.
These models live in a neutral package so the protocol stays separate from the
host server implementation and the guest agent runtime.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TaskAction(StrEnum):
    """Task actions supported by the first disposable VM implementation."""

    NOOP = "noop"
    COMMAND = "command"
    PYTHON_SCRIPT = "python_script"


class ArtifactMetadata(BaseModel):
    """Small artifact descriptor returned by the guest.

    Actual artifact upload is intentionally not implemented in phase 1; this
    schema reserves a stable place for later host-collected paths or URLs.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    path: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    content_type: str | None = None


class GuestHello(BaseModel):
    """Initial guest registration request."""

    model_config = ConfigDict(extra="forbid")

    guest_id: str = Field(min_length=1, max_length=128)
    hostname: str = Field(min_length=1, max_length=255)
    agent_version: str = Field(min_length=1, max_length=64)
    boot_id: str = Field(min_length=1, max_length=128)
    python_version: str | None = Field(default=None, max_length=128)
    python_executable: str | None = None
    platform: str | None = None

    @field_validator("guest_id", "hostname", "agent_version", "boot_id", mode="before")
    @classmethod
    def _strip_required(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class HelloResponse(BaseModel):
    """Task metadata returned by the host after /hello."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=128)
    action: TaskAction
    payload_url: str = Field(min_length=1)
    timeout_seconds: int = Field(gt=0)


class TaskPayload(BaseModel):
    """Payload downloaded by the guest before execution."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=128)
    action: TaskAction
    timeout_seconds: int = Field(gt=0)
    command: list[str] | None = None
    script_text: str | None = None
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("command")
    @classmethod
    def _validate_command(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("command must not be empty")
        if any(not isinstance(item, str) or not item for item in value):
            raise ValueError("command entries must be non-empty strings")
        return value

    @field_validator("env")
    @classmethod
    def _validate_env(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("env keys must be non-empty strings")
            if not isinstance(item, str):
                raise ValueError("env values must be strings")
        return value

    @model_validator(mode="after")
    def _validate_action_payload(self) -> TaskPayload:
        if self.action is TaskAction.COMMAND and self.command is None:
            raise ValueError("command action requires command")
        if self.action is TaskAction.PYTHON_SCRIPT and not self.script_text:
            raise ValueError("python_script action requires script_text")
        if self.action is TaskAction.NOOP and (self.command is not None or self.script_text):
            raise ValueError("noop action must not include command or script_text")
        return self


class GuestLog(BaseModel):
    """Log record uploaded by the guest."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=128)
    stream: Literal["agent", "stdout", "stderr", "controller"] = "agent"
    message: str
    timestamp: str | None = None


class GuestResult(BaseModel):
    """Final guest result uploaded to the host."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=128)
    status: Literal["completed", "failed", "timeout", "error"]
    exit_code: int
    stdout_tail: str = ""
    stderr_tail: str = ""
    artifacts: list[ArtifactMetadata] = Field(default_factory=list)
    duration_seconds: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


def utc_now_iso() -> str:
    """Return a compact UTC timestamp used in persisted controller logs."""

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
