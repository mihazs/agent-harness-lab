# Offline runtime fixture coverage

Run `python -m agent_harness_lab.runtime_compatibility` to report the current
Claude Code and Codex pins. For each pin the report lists every applicable
surface classifier rule as `passed`, `failed`, or `untested`, plus native and
generic capture parsers and declared loading signals. A rule is `passed` only when its named
synthetic fixture matches the rule and produces the expected classification.
The Codex capture fixture feeds a synthetic header and tool call through the
native parser. A synthetic Claude message exercises its parser and its declared
skill-request signal. No runtime, credentials, network, or private history is used.

The fixture versions are separate from the runtime pins. An unknown version or
a pin with no matching fixtures is `unsupported`; the command exits nonzero and
lists the affected rules as `untested`. It also exits nonzero for a failed
fixture. CI runs the report and its tests on every pull request, including pin changes.
Update fixtures and review their expected behavior when changing a pin; changing
a version label alone does not establish that the runtime behaves that way.

This is a report of the specific fixtures exercised, not a claim that a runtime
is compatible or that an agent loads a surface. Most rules, generic parser and
loading-signal entries remain untested. The Claude skill signal describes a declared request, not
activation. Passing synthetic cases do not establish live runtime
behavior, capture completeness, activation, model outcomes, or safety.
