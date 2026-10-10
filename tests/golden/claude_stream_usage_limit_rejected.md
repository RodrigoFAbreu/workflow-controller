# claude_stream_usage_limit_rejected.jsonl

A sanitised copy of the tail of a real worker stream that hit the Claude session limit
(`~/.local/state/workflow-controller/jobs/20260924T213209Z-148c63ed/worker.stdout`, 2026-09-24): an
`allowed_warning` `rate_limit_event` at 99%, a `rejected` one for `rateLimitType: "five_hour"`, the
synthetic `assistant` message with `error: "rate_limit"` and the `result` with `is_error: true` and
`api_error_status: 429`. Session and event ids are replaced, the transcript and usage figures are dropped,
and the epoch `resetsAt` values are the real ones. `controller.usage.read_claude_stream` reads it as 100%
for the five-hour window (from the structured `status`, never the text) and 14% for the weekly window.
