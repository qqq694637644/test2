# Portable Workflow Development Lessons

Last updated: 2026-06-04

This document is a project-agnostic playbook for developing GitHub Actions workflows that drive external machines, virtual machines, desktop applications, agents, or other long-running integration targets.

It intentionally avoids project-specific paths and product names. Use it when starting workflow automation in another repository to avoid repeating the same classes of failures.

## 1. Separate the workflow into failure domains

Do not treat a failed workflow as one large black box. Split the path into explicit domains and prove which one failed.

Typical domains:

```text
workflow_dispatch accepted by GitHub
runner allocated
repository checkout
project/package installation
host-side controller/service started
external machine or VM restored/started
agent connected to host
payload downloaded by agent
payload executed
application under test launched
application ready signal produced
API/GUI tests executed
cleanup completed
artifacts uploaded
workflow exit code mapped correctly
```

Practical rule: every run artifact should make it obvious which domain failed. For example, a state file with `agent_connected=false` and `payload_downloaded=false` means do not debug payload code yet.

## 2. Make every boundary produce a durable artifact

Logs shown in the GitHub UI are useful but not sufficient. Upload structured artifacts for every workflow run, including failures.

Recommended minimum artifacts:

```text
controller_state.json
agent_hello.json or equivalent handshake file
payload.json or payload metadata
result.json
stderr/stdout tails
host service log
external tool/application log tail
heartbeat.ndjson
restore/start metadata for VM or external target
```

Artifact design rules:

- Use JSON for machine-readable state.
- Use NDJSON for stage heartbeat streams.
- Include timestamps, stage names, timeout values, process IDs, command lines, and return codes.
- Include log tails instead of huge logs by default.
- Make failure artifacts upload even when earlier steps fail.

## 3. Use explicit readiness handshakes, not sleeps

Avoid fixed waits such as `sleep 60` as the primary readiness mechanism. They create both flaky failures and slow successful runs.

Better patterns:

```text
host service writes health endpoint
agent POSTs /hello or writes a ready file
application bootstrap writes ready.json
external process opens a known port and responds to /health
VM restore script writes restore metadata
```

Readiness files should include:

```text
status
base_url or endpoint
process_id
instance_id if applicable
input path or target identity
startup timestamps
version information
```

The controller should wait for readiness with a bounded timeout and record the last observed state.

## 4. Keep orchestration outside the application process whenever possible

If a workflow tests an application that hosts plugins, UI loops, event loops, or main-thread synchronization, keep the test driver outside that process.

Risky pattern:

```text
application process starts plugin
plugin starts server
test thread inside same process calls that server
server calls back into application main thread
test thread requests process exit
```

More robust pattern:

```text
application bootstrap starts plugin/server and writes ready.json
external test process waits for ready.json
external test process calls APIs / controls UI
external test process owns timeout and cleanup
external test process kills the application if needed
```

This avoids deadlocks between the test client, server thread, and application main thread. It also allows the workflow to collect logs and kill the application even if the application is partially hung.

## 5. Add heartbeat before adding coverage

Long-running workflow steps must report progress. A workflow that appears stuck is a test harness bug, even if the underlying application is healthy.

Recommended heartbeat stages:

```text
validate_inputs_start
validate_inputs_done
external_target_start
ready_wait_start
ready_seen
api_or_gui_test_start
api_or_gui_test_done
cleanup_start
cleanup_done
```

For each meaningful API or UI action, record:

```text
stage name
input summary
start timestamp
end timestamp
status or error
timeout used
```

If the workflow fails, the final result should contain `last_stage` or `heartbeat_tail`.

## 6. Start with a basic mode, then add full mode

Do not start by testing every feature. First prove that the transport, target startup, readiness, and cleanup path works.

Suggested modes:

```text
basic: startup, readiness, health, metadata/list operation, cleanup
full: heavier endpoints, invalid inputs, pagination, negative cases
mutation/destructive: database/file writes, patching, state changes, rollback checks
```

A good workflow exposes this as an input:

```text
test_mode=basic|full|mutation
```

Rules:

- `basic` should finish quickly and be safe to rerun often.
- `full` should remain non-destructive unless explicitly named otherwise.
- destructive/mutation tests should never be the default mode.

## 7. Use short, layered timeouts

A single large timeout hides the real failure point. Use a hierarchy of timeouts.

Example:

```text
runner job timeout: 30-45 minutes
integration step timeout: 3-5 minutes for smoke
agent connect timeout: bounded and explicit
application ready timeout: short and recorded
individual API call timeout: seconds, not minutes
cleanup timeout: short, then force-kill
```

Rules:

- Keep smoke timeouts short enough that a broken test fails quickly.
- Give heavy tests their own explicit mode and larger timeout.
- Always record timeout values in artifacts.
- Timeout handlers should collect log tails before killing processes.

## 8. Cleanup must be owned by the workflow, not by the target

Do not depend on the application under test to exit cleanly. The workflow should own cleanup.

Cleanup pattern:

```text
try graceful shutdown
wait a short time
terminate process
taskkill/kill process tree if still alive
record final process state
upload logs/artifacts
```

On Windows, consider process-tree cleanup:

```text
taskkill /PID <pid> /T /F
```

On Unix-like systems, consider process groups and signal escalation:

```text
SIGTERM -> wait -> SIGKILL
```

Cleanup must run in `finally` or a workflow step with `if: always()`.

## 9. Generated payloads need runtime tests, not just compile tests

Workflows often generate scripts dynamically. Compiling generated code catches syntax errors but not initialization-order failures.

Minimum tests for generated payloads:

```text
compile generated script
assert no unresolved placeholders remain in outer script
execute generated script in an early-failure scenario
assert it emits structured result JSON
assert it emits heartbeat before failing
assert it exits with expected non-zero code
```

Example early-failure cases:

```text
missing input file
missing application directory
unsupported mode
bad endpoint URL
invalid JSON input
```

These tests are cheap and prevent wasting runner/VM time on trivial payload bugs.

## 10. Normalize and validate operator inputs early

Operator input errors should fail before launching expensive external targets.

Common issues:

```text
URL missing scheme, e.g. host:port instead of http://host:port
invalid JSON string passed as workflow input
path exists on host but not on guest
snapshot name mismatch
unsupported test mode
empty required parameter
```

Recommended validation:

```text
normalize URL schemes
parse JSON workflow inputs before starting target
validate mode choices
validate paths in the environment that will use them
write validation result into artifacts
```

If input is used by the guest, validate it in the guest payload too; host-side validation alone may be insufficient.

## 11. Distinguish target-start failures from payload failures

The same workflow can fail before the payload ever runs. Do not confuse these cases.

Examples:

```text
VM restore failed -> no guest hello -> payload not downloaded
agent not running -> guest_connect_timeout -> payload not downloaded
payload placeholder bug -> agent result has Python exception
application launch failure -> payload result has application log tail
API assertion failure -> payload result has endpoint response details
```

Triage order:

1. Did the runner start?
2. Did checkout/install succeed?
3. Did external target restore/start succeed?
4. Did agent handshake occur?
5. Did payload download occur?
6. Did payload start executing?
7. Did application under test reach ready state?
8. Which endpoint/UI step failed?

Artifacts should answer each question without needing interactive access.

## 12. Make external-machine workflows manually dispatchable

For workflows that depend on local machines, VMs, licenses, GUI apps, USB devices, or private networks, `workflow_dispatch` is often safer than automatic push triggers.

Recommended inputs:

```text
controller_url or service_url
port
run_restore=true|false
restore_script or startup_script
restore_extra_args_json=[]
task_action or test_mode
target_path
timeouts
```

Rules:

- Use defaults for the common case.
- Keep escape hatches for debugging, such as `run_restore=false`.
- Represent extra arguments as JSON arrays to avoid shell quoting problems.
- Record the final resolved inputs in artifacts.

## 13. Be careful with new workflow files and GitHub indexing

A newly added workflow file may not be dispatchable immediately in every context.

Observed failure patterns:

```text
404 when dispatching by workflow filename
422 saying workflow does not have workflow_dispatch
workflow id works later after GitHub indexes the file
```

Recommendations:

- Once the workflow has a stable ID, prefer dispatch by workflow ID.
- Verify the workflow is visible in the Actions UI or API before assuming the YAML is wrong.
- Avoid adding unrelated push triggers only to work around dispatch indexing unless absolutely necessary.
- If you add a temporary trigger, remove it after the workflow can be dispatched directly.

## 14. Keep support files out of plugin scan roots

For any tool that scans a directory for plugin entry files, do not place helper modules beside real plugin entries unless the tool supports that layout.

Generic rule:

```text
plugins/
  real_plugin_entry.py
  real_plugin_support/
    __init__.py
    helper_a.py
    helper_b.py
```

Avoid:

```text
plugins/
  real_plugin_entry.py
  helper_a.py
  helper_b.py
```

Because many plugin systems will try to load every top-level file as a plugin.

Installer rules:

- Install only the actual entry file at the plugin root.
- Put support files in a package/subdirectory.
- Remove legacy root-level support files during upgrade.
- Test a clean install and an upgrade install with old files present.

## 15. Treat unrelated third-party warnings as noise, but document them

External environments may have other plugins, drivers, services, or startup scripts that print warnings.

Rules:

- Do not fail your workflow on unrelated warnings unless they break your target behavior.
- Do fail on your own plugin/module import errors, missing entrypoints, or failed readiness.
- Document known unrelated warnings so future maintainers do not chase them again.

A good result should say:

```text
Known unrelated warning observed: <warning>
Target checks still passed: true
```

## 16. Do not mix non-destructive and destructive tests

Non-destructive smoke tests should be safe to rerun and should not mutate persistent state.

Examples of non-destructive checks:

```text
health
metadata
list/pagination
read-only query
invalid input returns structured error
unsafe endpoint is rejected by default
unknown route returns 404
```

Destructive or mutation checks need a separate workflow mode:

```text
mutation_test=true
isolated temporary database/file
explicit fingerprint or version check
rollback or disposable environment
post-mutation verification
post-test cleanup
```

Never add destructive behavior to the default full smoke by accident.

## 17. Negative cases are part of success

A robust workflow should verify that unsafe or invalid requests fail correctly.

Useful negative cases:

```text
invalid address or ID
invalid pagination offset
unsupported enum value
unknown route
unsafe execution disabled by default
missing file path
bad JSON input
unsupported mode
```

Assertions should check that the failure is structured and predictable, not just that an exception occurred.

## 18. Keep root-level operational memory

For long workflow development sessions, keep root-level documents that survive context compression and handoff.

Recommended split:

```text
PROJECT_STATUS.md              # current implementation status
PROJECT_TEST_PROGRESS.md       # chronological run log
PROJECT_WORKFLOW_LESSONS.md    # project-specific operational memory
PORTABLE_WORKFLOW_DEVELOPMENT_LESSONS.md  # transferable patterns
```

Each real run entry should include:

```text
Run URL
Commit SHA
Inputs
Conclusion
Artifact ID
Result summary
Failure / next action
```

This prevents repeated debugging after context is lost.

## 19. Minimal reusable checklist

Before running an external-target workflow:

```text
[ ] workflow_dispatch inputs are explicit and validated
[ ] URL inputs include scheme or are normalized
[ ] external target restore/start has its own artifact
[ ] agent handshake is recorded
[ ] payload metadata is recorded
[ ] readiness is explicit, not sleep-based
[ ] heartbeat is written before expensive operations
[ ] per-stage timeouts are short
[ ] cleanup is in finally / if: always()
[ ] artifacts upload on failure
[ ] generated payload has compile and runtime early-failure tests
[ ] support files are not placed in plugin scan roots
[ ] basic mode passes before full mode
[ ] full mode is non-destructive unless clearly labeled otherwise
[ ] destructive tests have a separate isolated plan
```

## 20. When a workflow fails, use this decision tree

```text
No workflow run created?
  -> Check workflow indexing, workflow id, workflow_dispatch trigger, branch/ref.

Run queued or no runner?
  -> Check self-hosted runner labels and online state.

Checkout/install failed?
  -> Fix repository or dependency bootstrap before external target work.

External target did not start?
  -> Read restore/start metadata; do not debug payload yet.

Agent did not connect?
  -> Check network URL, URL scheme, firewall, agent autostart, target power state.

Payload not downloaded?
  -> Check controller routing and agent job id.

Payload crashed before target launch?
  -> Check generated payload placeholders, imports, early validation.

Target launched but no ready signal?
  -> Check target log tail, readiness file path, startup dialogs, license prompts.

Ready signal exists but API/UI test failed?
  -> Check endpoint response, negative-case behavior, and per-stage heartbeat.

Workflow timed out?
  -> Shorten stages, add heartbeat, move tests outside target process, force cleanup.
```

## Current takeaway

The most transferable lesson is that reliable workflows for external systems are built around **explicit boundaries**:

```text
start -> handshake -> payload -> ready -> test -> cleanup -> artifact
```

Every boundary needs a status file, timeout, and artifact. Without those, failures look like random hangs; with them, each failure becomes a small, fixable bug.
