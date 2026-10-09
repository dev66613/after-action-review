# Evidence-based review

Read this when a task/goal boundary, checkpoint, repeated error, blocker or user
feedback triggers an AAR. The hook's IDs identify the original session and turn.
Use them for the report even when Stop created a new continuation turn.

If SessionStart has not supplied completion IDs and status has no matching
boundary event yet, do not invent IDs. Deliver the primary result; the trusted
Stop hook supplies the original session/turn IDs for the bounded review.

1. Read `aar_status` and evidence already present in the conversation. Identify
   whether this is task completion, a checkpoint, interruption, blocked work or a
   survey reply. A Stop event is a turn boundary, not proof of goal completion.
2. Compare expected and observed results against actual acceptance checks. Assess
   correctness, efficiency, user friction and reuse value as applicable. Avoid
   invented scores or percentages without a denominator.
3. Record each material finding's concrete trigger, observed failure/waste,
   likely cause, source/test/message IDs, counter-evidence, recurrence, confidence
   and action. Separate problem severity from action risk. Search exit 1 may
   mean no matches; inspect before calling it a defect. Equal exit codes alone
   do not prove different commands had the same failure.
4. Fix confirmed task defects under existing authorization. For reusable process
   improvements use changes.md and `aar_propose`. Avoid universal rules based on
   one anecdote; keep operational lessons short and specific.
5. Call `aar_review` with expected, observed, outcome, findings, verification and
   limitations. Findings include applied/queued change IDs. Use outcome=checkpoint
   for unfinished work and survey=false for checkpoints, internal maintenance,
   acknowledgements and survey replies. Native goal status reflects actual work.
6. At finished/blocked user tasks, ask one optional survey using feedback.md.
   Keep required decisions separate; resume the primary task when work remains.

Ordinarily take one focused pass and reuse fresh successful checks. Research only
an important unresolved uncertainty; do not start a research run for every AAR.
No subagents for routine reviews. After two failed fixes reassess the hypothesis.
Stop permits at most one continuation per external user turn. If a review cannot
finish, retain pending events and disclose the gap instead of manipulating host
flags/internal markers. Interrupt/session-end events resume on a later authorized
turn, not by restarting work the user stopped.

The detector stores fingerprints/tool names/error classes, not raw prompts,
complete tool inputs/outputs or transcripts. Read original evidence in the current
chat; absent past context is a coverage gap. Long sessions are checked at tool
activity/compaction rather than a timer waking idle chats. The active agent must
report semantic blockers that produce no tool error.

## CLI fallback when MCP is unavailable

Use the bundled script with one UTF-8 JSON object. `review` does not have
`--session-id` or `--turn-id` options; those fields belong in the input JSON.
Prepare a small file in the authorized workspace and run:

```sh
python3 /absolute/plugin/scripts/aar.py review --input /absolute/workspace/aar-review.json
```

Replace both paths with real paths derived from this skill and the workspace.
On Windows use `python` instead of `python3`. Input has this shape; substitute
the actual hook IDs and observed evidence:

```json
{
  "session_id": "<original session_id from hook>",
  "turn_id": "<original turn_id from hook>",
  "survey": true,
  "report": {
    "expected": "<requested outcome>",
    "observed": "<verified result>",
    "outcome": "success",
    "findings": [],
    "verification": [],
    "limitations": []
  }
}
```

Choose the actual outcome and use survey=false for a checkpoint or survey reply.
An omitted --input reads closed stdin; `--input -` would try to open a file named
`-`. Check the returned review_id, outcome and survey before reporting success.
Respect a native denial; record an unavailable review rather than bypassing it.
