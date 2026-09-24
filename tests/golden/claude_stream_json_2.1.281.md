# `claude_stream_json_2.1.281.jsonl`

A real `claude -p --output-format stream-json --verbose` transcript, captured once at CP4 of
`workflow-controller-release-runtime-observability`
(`docs/ai-workflow/CONTROLLER_RELEASE_RUNTIME_OBSERVABILITY_PLAN.md`, "CP4 -- streaming worker
output"). `tests/test_worker.py`'s real-CLI golden test parses it with
`controller.worker._parse_worker_stream` and classifies it with `_classify`.

- **CLI version:** `claude --version` reported `2.1.281 (Claude Code)`.
- **Captured:** 2026-09-24, from an empty scratch directory, stdin closed. Exit code `0`, empty
  stderr, seven lines (`system/init`, two `system/thinking_tokens`, two `assistant`, one
  `rate_limit_event`, one `result`). Cost reported by the result event: USD 0.0176.
- **Command:**

  ```
  cd <empty scratch dir>
  claude -p "Reply with the word ok." --output-format stream-json --verbose --model haiku \
      < /dev/null > raw.jsonl 2> raw.err
  ```

## Sanitisation

Every line keeps its structure: the same keys in the same order, the same value types, and every
number, boolean and `null` unchanged. Only these string values were replaced:

- every UUID (session ids, event `uuid`s) by `00000000-0000-4000-8000-<n>`, numbered in order of
  first appearance, so equal ids stay equal (the session id is `...-000000000001`);
- message ids `msg_...` by `msg_PLACEHOLDER` and request ids `req_...` by `req_PLACEHOLDER`;
- the thinking block's `signature` by `SIGNATURE_PLACEHOLDER`;
- the scratch directory by `/work/target` (and its project-directory slug by `-work-target`),
  the home directory by `/home/user`, the runtime directory by `/run/user/1000`, and the
  messaging socket's file name by `0.sock`.

Each line is re-serialised compactly (`separators=(",", ":")`, `ensure_ascii=False`), as the CLI
emits it.
