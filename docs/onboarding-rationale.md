# Newcomer experience: evidence and decisions

Reviewed 2026-11-02. These changes aim to reduce setup and orientation friction;
no adoption, retention or star-growth effect has been measured.

| Source | Relevant finding or guidance | AHL change |
| --- | --- | --- |
| [Fronchetti et al., ESEC/FSE 2023](https://doi.org/10.1145/3611643.3616288), [author manuscript](https://www.ime.usp.br/~gerosa/papers/FSE2023_Fronchetti.pdf) | Contributor documentation often omits task-selection and community-contact guidance. | Link starter issues, contribution steps and the actual support channel. |
| [Syracuse University OSPO](https://opensource.syracuse.edu/best-practices/) | Explain the purpose, audience, prerequisites and first executable example; keep contributions approachable. This is practitioner guidance, not causal evidence. | Lead with a concrete purpose, installation and project commands; retain a credential-free example. |
| [GitHub issue-form syntax](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms) | Forms support typed fields and required validation. This is a platform contract, not adoption evidence. | Use focused bug/feature forms; keep implementation proposals optional for reporters. |

The growth recommendations rely on academic/institutional sources, not vendor
marketing or promised growth rates. GitHub documentation is used only for its
own platform's syntax. Avoid treating documentation presence as proof of quality.

## Follow-through from zero

1. Make the first documented experience reproducible without credentials; show
   actual output and explain what it does and does not establish.
2. Ask volunteer users to try installation and the example. With consent, record
   completion, failed steps and time to first report. Do not install telemetry.
3. Fix the most repeated setup failure before broad promotion. Keep a small set
   of clearly scoped starter issues and answer concrete contributor questions.
4. Share a reproducible demonstration in relevant communities only after checking
   their current rules and obtaining publication authorization. Do not promise
   automatic application while it remains under development.
5. Review voluntary reports of repeat use and completed external contributions.
   Stars and traffic provide context; they do not establish successful use.

Existing [community growth plan](community-growth.md) owns the broader roadmap.
The immediate product blocker is [#77](https://github.com/mihazs/agent-harness-lab/issues/77),
with first-run setup in [#142](https://github.com/mihazs/agent-harness-lab/issues/142)
and skill parity in [#143](https://github.com/mihazs/agent-harness-lab/issues/143).
