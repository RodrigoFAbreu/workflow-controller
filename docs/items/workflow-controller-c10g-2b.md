# C10g-2b: `verify-codex`, the command and its probes, with verify-on-first-use cached by the build's digest

Item id: `workflow-controller-c10g-2b`.

## Roadmap row (moved verbatim)

Complexity: medium ([#217](https://github.com/RodrigoFAbreu/workflow-controller/issues/217)). `verify-codex`, the command and its probes, with verify-on-first-use cached by the build's digest (`workflow-controller verify-codex [--force] [--codex-binary PATH]` runs the C10g-1 probes in fresh jobs against host fixtures, records the digest in `verified-codex-builds.json` only when every probe holds and the build is unchanged, and refuses by name otherwise; nothing launches on the record, `build_verification` for `codex` stays `none` until C10g-3; a `feat:` change, released as a minor version)

Needs: C10g-2a

Section: [11.9](../roadmap-archive.md#119-automated-functional-review)
