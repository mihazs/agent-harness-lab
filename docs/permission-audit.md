# Permission-result audit

`--permission-audit permission-results-v1` requests an optional Linux x86_64
controller observation profile for `ahl evaluate --agent offline` or
`--agent responses`, and controlled Responses `ahl improve`/`watch`.
`sandbox-check` accepts the same option. The profile requires an explicitly
pinned runtime image, delegated cgroup and aggregate private disk bounds.
Unavailable tracing, unsupported ABI, incomplete capture or exhausted bounds
block qualification; execution never falls back to an unaudited worker.

This profile observes kernel syscall results from the role and its traced
descendants. EACCES, EPERM and EROFS in the role are permission-result violations,
including errors caught by a worker that eventually exits zero. The artifact
grader outcome stays separate. A violated/unknown qualification prevents a
successful evaluator exit or improvement recommendation. Controlled inference
stops after an unsuccessful proposer, evaluator-author or executor command
qualification. A grader's violated or unknown audit blocks final qualification;
remaining scheduled trials can still run. Automatic promotion stays off.

The observer does **not** infer malicious intent or complete authorization
coverage. ENOENT, absent mounts, denied reads that return a different result,
semantic tool permissions, transient successful writes, unobserved outside
processes and native model identity remain unverified. A passing task or a
`qualified` permission-result capture cannot establish full role-isolation
certification, independence or a safe general security posture. Expected negative
tasks can pass their artifact grade while violating this frozen audit policy.

## Frozen execution profile

The dedicated standard-library monitor runs under host Python `-I -S`, outside
the role namespace. Its worker joins the existing cgroup before namespace launch;
existing tmpfs quotas, pinned-image checks, stdout/stderr ceilings, parent deadline
and whole-cgroup cleanup remain required. No Python `preexec_fn` runs in the
controller's possibly threaded process for this profile.

The controller's canonical top-level `/usr/bin/python3` argv receives `-B` before
its existing arguments. This is an explicit runtime-profile change: it prevents
Python `-I` imports from attempting cache writes to the readonly image. Requested
and effective argv have separate hashes in the private attestation. Other
executable spellings are unchanged; arbitrary descendants can ignore the inherited
`PYTHONDONTWRITEBYTECODE=1` environment and their cache denials remain role events.
This is not globally execution-transparent and does not authenticate Python or a
model. No argv text authorizes exceptions.

A five-step exact exec-argv hash chain covers outer bubblewrap, trusted quota
bootstrap, inner bubblewrap, quota verification and the requested effective
command. Setup denials remain labelled setup. After the final trusted transition,
role attribution is sticky across fork, clone and exec. Plain role SIGTRAP is
forwarded; unsupported job-control stops and ABI changes cannot qualify. clone3
is blocked with ENOSYS to permit the known classic-clone fallback;
CLONE_UNTRACED is blocked with EPERM. The existing native-ABI/HugeTLB filter
remains in force. Policy and filter hashes identify these fixed semantics.

Completion is scoped to role permission results; setup telemetry is never certified.
There is one narrow setup race: after role admission, a nonroot single-thread
phase-1 bubblewrap helper waiting in `wait4` can die while syscall information is
being read. Only GET_SYSCALL_INFO ESRCH in that exact class may quarantine the
still-owned TID and entry. The monitor neither resumes nor retires it until a
mandatory owned terminal wait proves its fate. The private `setup_gaps` records
that surrogate task, phase, last syscall and terminal exit/signal status;
`setup_coverage` is incomplete and `role_coverage` can independently be complete.
Any subsequent stopped progress, exec remap, missing wait, deadline, root loss,
other phase/syscall or role information loss keeps qualification unknown. This
cannot establish complete setup capture or absence of setup denials.

## Private observations and limits

A fresh nonce-bound CLOEXEC pipe carries the trusted monitor report. The role
receives neither its writer nor the launch frame. Worker stdout is a separate
untrusted channel and cannot supply an audit receipt. The controller accepts only
bounded, correctly typed completion metadata with the expected profile, role,
nonce and policy hash. Imported JSON does not establish live observation.

The policy caps the launch frame at 128 KiB, audit result at 64 KiB, events at 128,
created tasks at 256 and syscall stops at 200,000. Each admitted run reserves an unknown capture before preparation or launch;
exceptions retain that unknown in cumulative and trial qualification. Completed
observations update the same capture, and earlier observed violations remain sticky.
Deadline/cap/protocol loss and unreaped or unknown tracees withhold complete qualification. Events contain only
controller ordering, surrogate task number, setup/role stage, ABI, syscall number,
errno and event category. Paths, arguments and file contents are omitted; the
results remain private. `hidden_access_attempts` remains unverified.

The monitor waits only for owned tracees. EXITKILL, controller/monitor parent-death
checks and normal outer cgroup cleanup protect stopped and running descendants.
A controller killed by SIGKILL cannot write a final report or remove its cgroup
directory; the tracee processes must still die, and no such interrupted run can
qualify. The operator remains responsible for the delegated subtree lifecycle.

## Checks

Run native protocol and compatibility controls:

```sh
task test:sandbox:permission-audit
```

Request actual disposable delegated kernel canaries:

```sh
task test:sandbox:kernel:permission-audit
```

These use synthetic workers only. The kernel target exercises pinned image and
quota-backed execution, swallowed readonly denial, allowed commands, descendant
exec, signal handling, controller/monitor loss, unsupported ABI and bounded output
and deadlines. It does not call models or use credentials. Default invocations
leave the audit unrequested and permission qualification unknown.
