#!/usr/bin/env bash
# Read-only harness audit for pull requests: no model call, no network, no secrets, and the private report
# goes to a temporary state directory outside the checkout. Findings become workflow annotations.
set -u
runtime="${INPUT_RUNTIME:-codex}"
state="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/ahl-audit.XXXXXX")" || exit 2
trap 'rm -rf "$state"' EXIT
changed=()
if git -C "$GITHUB_WORKSPACE" diff --name-only -z HEAD^1 HEAD >"$state/changed" 2>/dev/null; then
  changed=("$state/changed")
fi
cd "$GITHUB_ACTION_PATH" || exit 2
XDG_STATE_HOME="$state" python3 -m agent_harness_lab audit "$GITHUB_WORKSPACE" --runtime "$runtime" --json \
  >"$state/report.json" 2>/dev/null
code=$?
if [ "$code" -ne 0 ]; then
  echo "::error title=ahl audit::The static audit exited with status $code; no findings are reported."
  exit "$code"
fi
python3 -m agent_harness_lab.audit.annotations ${changed[@]+"${changed[@]}"} <"$state/report.json"
