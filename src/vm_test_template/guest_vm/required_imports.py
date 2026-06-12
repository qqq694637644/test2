"""Required third-party imports for the disposable VM guest snapshot.

Run this module inside the guest VM before taking the clean snapshot:

    py -3.11 -m vm_test_template.guest_vm.required_imports

The guest agent does not auto-install dependencies because the snapshot should
contain a known stable runtime.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class RequiredImport:
    """A guest-side import and the package spec that provides it."""

    import_name: str
    package_spec: str
    reason: str


GUEST_REQUIRED_IMPORTS = (
    RequiredImport(
        import_name="requests",
        package_spec="requests>=2.32.0",
        reason="HTTP client used by guest_vm.agent to call the host controller",
    ),
)


def missing_guest_imports() -> list[RequiredImport]:
    """Return guest imports that are not available in the current interpreter."""

    return [
        item
        for item in GUEST_REQUIRED_IMPORTS
        if importlib.util.find_spec(item.import_name) is None
    ]


def verify_guest_imports() -> None:
    """Import every required module and raise ImportError if any dependency is missing."""

    for item in GUEST_REQUIRED_IMPORTS:
        importlib.import_module(item.import_name)


def install_command() -> str:
    """Return the pip command to prepare the guest VM before taking a snapshot."""

    specs = " ".join(item.package_spec for item in GUEST_REQUIRED_IMPORTS)
    return (
        f"{sys.executable} -m pip install --disable-pip-version-check {specs} "
        "--proxy http://192.168.1.249:10810"
    )


def main() -> None:
    missing = missing_guest_imports()
    if missing:
        print("Missing guest VM snapshot imports:", file=sys.stderr)
        for item in missing:
            print(
                f"  {item.import_name}: install {item.package_spec} ({item.reason})",
                file=sys.stderr,
            )
        print("Install command:", file=sys.stderr)
        print(f"  {install_command()}", file=sys.stderr)
        raise SystemExit(1)

    verify_guest_imports()
    print("Guest VM required imports are available:")
    for item in GUEST_REQUIRED_IMPORTS:
        print(f"  {item.import_name} ({item.package_spec})")


if __name__ == "__main__":  # pragma: no cover
    main()

