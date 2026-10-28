# The command files

The Workflow's commands are plain Markdown files in `.claude/commands/`, one per
command. They are written so that a tool other than the one that created the
directory can read the same files, map their frontmatter to its own settings,
substitute the arguments and send the body to a model as the prompt, with no
per-harness copy. This document is the contract such a tool relies on. It is also the
document the Workflow's own conformance test checks the command files against.

## Purpose and who reads it

The reader is the author of an **adapter**: the part of a tool (for example the
Workflow Controller) that launches a command for a worker under a harness other than
the one that created `.claude/commands/`. A person who only runs the commands needs
none of this; `WORKFLOW_V2_1_OPERATOR_REFERENCE.md` is theirs.

## Terms

- **The agent**: whatever carries out the steps of a command body, a model running
  under some harness. The bodies and the other shipped documents use this word in
  its plain sense and do not each repeat a definition. It is never the user.
- **The user**: the person whose own current-turn message a command may require.
- **The harness**: the program that runs the agent: it lists the commands, applies the
  frontmatter, substitutes the arguments and sends the body as the prompt.
- **The adapter**: the code that does that for one harness.

## The file format

A command file is the frontmatter, then the body. The frontmatter sits between two
lines that contain only `---`, and is single-line `key: value` pairs (a value may be
quoted). The body is everything after the closing line. The body is the prompt.

## The keys

| Key | Class | What an adapter does |
|---|---|---|
| `disable-model-invocation` | **Honour** | `true` (nine commands): never launch the command for a model on the model's own initiative or as a step of another command; launch it only on the user's own request. An adapter that cannot tell refuses the command. The command's own confirmation check stays on regardless. |
| `state_writer` | **Honour** | A command file declares `true` or `false` only (`"publisher"` is a script-side value and never appears in a command file). `true`: the command changes `WORKFLOW_STATE.json`, always through `workflow_state.state_transaction`. Launch it in the checkout whose state it changes, with permission to run the Workflow scripts and Git there; never give the worker another way to write the state file. `false`: it never writes that file. The Workflow's conformance check requires the key on every command. |
| `review-subject` | **Carry** | `bundle`, `verdict` or `none`: what the command reviews or consumes. Read by the Workflow's own conformance check (which commands call `assert_bundle_not_rejected`). An adapter passes the file through unchanged and needs no action. |
| `description` | **Hint** | One line, shown when listing commands. No semantics. |
| `argument-hint` | **Hint** | The argument shape, for display and prompts. The grammar the body enforces is in the body. |

The three classes: **Honour** means the adapter must act on the key; **Carry** means
the key is read by the Workflow's own checks and the adapter passes it through;
**Hint** means display only.

Rules for adapters:

- An unknown key is ignored and never fails the launch.
- The Workflow adds a key only with a row in the table above; a test fails when a key
  is in use without a row, or a row names a key no command uses.
- A key's class never changes silently.

## `$ARGUMENTS`

`$ARGUMENTS` is the Workflow's argument placeholder.

- **Where:** the literal token `$ARGUMENTS`, in the body only, never in the
  frontmatter.
- **What an adapter substitutes:** the argument string exactly as the user typed it
  after the command name, verbatim and unparsed, at every occurrence of the token. The
  body does the parsing (a work item id, `plan|implementation`, a base commit).
- **Empty:** no argument given is substituted as the empty string. The body's own
  argument grammar decides what that means. A body that requires an argument refuses
  without it, and a body whose argument is optional applies its own stated default:
  for most, the active work item; `milestone-plan.md` identifies the milestone from
  the active-milestone, roadmap and backlog documents; `prepare-review.md` uses the
  flat review path with no work item (except at the plan stage, where the id is
  required) and never resolves one from
  `active_work_item_id`. There is no universal default, and not every body spells out
  its empty case.
- **Rule for bodies:** every command whose `argument-hint` is non-empty names
  `$ARGUMENTS` in its body, so no adapter has to append the arguments after the body.
  A command with no hint takes no arguments.

An adapter that appends the arguments anyway is harmless: the token is substituted and
the appended copy is redundant.

## The user-only commands

Nine commands carry `disable-model-invocation: true`:

- `/accept-milestone`
- `/adopt-gate-policy`
- `/approve-review`
- `/archive-finished-item`
- `/prune-archives`
- `/release-checkpoint-start`
- `/request-plan-amendment`
- `/resume-implementation`
- `/retire-legacy-work-item`

The control each of them carries itself, under every harness, is the
literal-confirmation check: the command refuses to write anything unless the user's
own current-turn message supplies the literal confirmation text its body names. The
flag is an additional layer where the harness enforces it; it does not replace the
check. The agent must never invoke any of them on the user's behalf, including as a step
of another command's execution, whatever the flag says. How the flag is enforced
differs by harness, and the harness notes below record what is known per harness.

## The Claude Code note

Claude Code is the harness that created `.claude/commands/`. It substitutes
`$ARGUMENTS` in a body that has the token, and appends the argument string to a body
that has none.

What is known about how it enforces `disable-model-invocation: true`
(`docs/ai-workflow/WORKFLOW_V2_PLAN.md`, D2, resolves `OPUS-R6-010`): this
installation's bundled frontmatter reference documents the flag as blocking only "the
SlashCommand tool" and does not name the `Skill` exposure path. The installed Claude
Code changelog separately confirms skill-aware handling of the flag (a fix for skills
with that flag failing when invoked via `/<skill>` mid-message), suggesting real
coverage there too. A same-session empirical test was inconclusive: that harness's
model tool surface exposes commands exclusively through the `Skill` tool (no distinct
`SlashCommand` tool is present in the available tool set), but the skill-routing table
is fixed at session start, so a newly-flagged command reliably returns "Unknown skill"
whether or not the flag actually gates it. A decisive test requires invoking a flagged
command through `Skill` from a fresh session after the file lands. Until that
fresh-session result is recorded, the confirmation check is the control that holds for
the `Skill` exposure path, not the flag alone.

## Neutrality rules

The command bodies and the shipped documents they point to name the model "the agent",
never by a product's name, and name no harness product or harness tool. A test fails
on the model's name (the word `Claude` not followed by `-Session`), the harness tool
name `SlashCommand` and the harness product name, in every command file (frontmatter
included) and in `MILESTONE_WORKFLOW.md`, `REVIEW_PROTOCOL.md` and
`WORKFLOW_V2_1_OPERATOR_REFERENCE.md`. This document is the one place that names them.

What stays, because it is an identifier and not the model: the `Claude-Session:`
trailer key; the `.claude/` directory in a path; a model-id example such as
`anthropic/claude-opus-5-5` (lower case, a value format).
