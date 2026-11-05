# C10g-3a: The tester's composed-configuration check

Item id: `workflow-controller-c10g-3a`.

## Roadmap row (moved verbatim)

Complexity: medium ([#221](https://github.com/RodrigoFAbreu/workflow-controller/issues/221)). The tester's composed-configuration check (`check_effective`): the configuration Codex ends up using once user, project and managed layers are composed is checked against the pinned tester profile, which narrows the one entry of `codex_sandbox.KNOWN_LIMITS` to `composition_window` (split from the old C10g-3, which was sized "low" but is three items) (`check_effective` compares the composed `config/read` result with values built by the profile and argv functions, and `check_composed` runs the `app-server` read and checks the home again; it is a separate control from `check_launch`, nothing calls it until C10g-3b; a `chore:` change with no release)

Needs: C10g-2a, C10g-2b

Section: [11.9](../roadmap-archive.md#119-automated-functional-review)
