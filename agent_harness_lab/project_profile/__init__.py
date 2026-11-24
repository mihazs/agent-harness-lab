"""Deterministic project profile and harness findings built from it (#1113).

`detect` describes what a checkout declares (technologies, package managers, declared and CI commands, layout) from
manifests alone; `harness_text` reads the instruction files and what they mention; `findings` compares the two
(lines the repository contradicts, facts the harness never states); `session_commands` and `history` add the commands the
agent really ran. Everything is read-only, bounded, standard library and model-free. A finding is a hypothesis that a
harness line is wrong or missing, never a diagnosis and never evidence that a change helps: the paired evaluation, the
fresh confirmation and the install review still decide.
"""
