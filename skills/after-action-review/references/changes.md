# Apply and rollback

Read before any process-change proposal, approval or rollback.

Initial policy: auto-apply low-risk skill, prompt, workflow and workspace-document
changes within explicitly registered owned directories; confidence at least 0.90,
two distinct evidence sources and a causal explanation. Confidence is a routing
threshold, not a calibrated probability. Default limits: five changes/day, 16 KiB
per patch. Config, hook, tool and workspace-code changes require a user decision.

Default scope is private `lessons/active.md`: its journal-verified content is loaded
on later sessions. Setup can register owned skill/prompt directories under existing
user authorization. Never expand scope just to make a rejected proposal eligible.
Do not target installed caches, synced `sources/`, system skills or core policy.

Read current bytes and calculate SHA-256, or `absent` for a new file. Prepare the
smallest useful replacement. Attach actual evidence including a behavioral
reproduction/check where practical and an independent source or user correction.
Explain scope/counter-evidence. Validate examples literally when safe; test
commands with side effects in isolated fixtures.

Call `aar_propose` with title, kind, risk, confidence, reason, evidence, target,
before_sha256, after and validation (markdown, skill, json, toml or python). The
engine parses structurally, checks scope/current hash, journals and backs up before
atomic replacement, then checks the result. It never executes candidate code or
model-supplied commands. Parsing alone is not behavioral proof.

After apply, inspect validation and ID and check real behavior in the authorized
environment. On confirmed regression call `aar_rollback` and record the finding.
Rollback checks the current after hash; conflicting later user edits are preserved.
Resolve conflicts with a reconciled proposal, not an overwrite. A subsequent
improvement requires new evidence.

For needs_review, show the exact diff, reason, expected benefit, risk, affected
scope, checks and rollback plan. Ask apply/revise/reject. Use
`aar_apply(approved=true, user_decision=...)` only after the human accepts this
exact change; record their actual decision. Approval does not relax hard
restrictions or authorize publishing or production writes.

`aar_changes` returns timestamps, evidence, hashes/status and rollback IDs.
`aar_recover` handles incomplete transactions when hashes match; conflicts remain
visible. State/backups stay outside the plugin version cache. Keep sensitive data
out of evidence/reports; redaction is only an extra backstop.

Do not self-modify engine, safety policy or hook trust from an AAR. Plugin updates
use a separately requested versioned update, tests and native installation/review.
Each additional execution host needs its own registered scope and trust.
