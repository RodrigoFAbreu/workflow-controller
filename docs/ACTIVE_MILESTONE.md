# Active Milestone

## Status

**In progress.** `workflow-controller-cleanup-3b` (CU3b: `verify-claude` diagnostics and small gaps, closes issues #132
and #133), plan revision 1, in `docs/ai-workflow/CONTROLLER_CLEANUP_3B_PLAN.md`. Checkpoints:

- CP1 (a host effect outranks a sandbox error, and the listener folder is short): complete. `python3 -m unittest
  tests.test_claude_build` passes, also with `TMPDIR` set to a 110-character folder; the new host-effect and deep-`TMPDIR`
  tests fail against the base commit.
- CP2 (a setup failure is exit 20, and `--claude-binary` stands alone): complete. `python3 -m unittest
  tests.test_claude_build` passes; the setup-`OSError` rows (clone files, output folder, listener bind, through the
  command) and the `--claude-binary`-with-empty-`PATH` note test fail against the base commit.
- CP3 (the live tests skip without credentials; the documentation, the roadmap): complete. With an empty scratch
  `CLAUDE_CONFIG_DIR` and `CONTROLLER_LIVE_CLAUDE_TESTS=1`, the five live tests skip instead of erroring;
  `python3 tools/check_docs.py` passes.

## Next action

`/milestone-implement workflow-controller-cleanup-3b` to enter self-review and prepare the implementation bundle.
