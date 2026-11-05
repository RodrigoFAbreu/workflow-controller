# CU3b: Clean-up, part 2

Item id: `workflow-controller-cu3b`.

## Roadmap row (moved verbatim)

Complexity: medium. Clean-up, part 2 (a `fix:` patch, accepted 2026-10-24 by gate policy, pull request #148 `fix: verify-claude names a host write before a sandbox error, takes --claude-binary without claude on PATH and exits 20 on a setup error (#132, #133)`): `verify-claude` diagnostics ([#132](https://github.com/RodrigoFAbreu/workflow-controller/issues/132): a sandbox-start error is checked before host effects) and its small gaps ([#133](https://github.com/RodrigoFAbreu/workflow-controller/issues/133): `--claude-binary` still needs a `claude` on `PATH`, an `OSError` while the probe run is built escapes as a traceback instead of exit 20, a wrong line in the development guide; done in CU3b, closing #132 and #133: a host effect outranks a sandbox-start error, the listener folder is short, a setup `OSError` is exit 20, `--claude-binary` stands alone, the live tests skip without credentials)

Needs: CU3a

Section: [Clean-up milestones](../roadmap-archive.md#at-a-glance)
