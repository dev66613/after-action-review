# Local data and privacy

After Action Review is a local plugin. Its Python runtime does not make network
requests or send telemetry. The Codex host and model provider handle their own
processing under their respective settings and policies.

| Stored locally | Purpose |
| --- | --- |
| Event IDs, timestamps, turn/session IDs, and limited failure metadata | Trigger and deduplicate reviews |
| Agent-written AAR reports and user survey replies | Preserve findings and feedback |
| Registered paths and policy settings | Define the allowed improvement scope |
| Proposed content, diffs, hashes, decisions, and previous file versions | Explain changes and support guarded rollback |

The collector does not retain raw prompts, full transcripts, or complete tool
outputs. Reports and proposals are provided by the agent and can contain
sensitive material; redaction is best effort. Backups intentionally retain the
previous contents of changed files. Do not register credential directories or
put private source material into findings.

Default state lives at `~/.local/state/codex-aar` on the Linux/WSL executor.
Directories use `0700` and private files use `0600` on POSIX systems. These
permissions restrict ordinary access by other users; they do not encrypt data
or defend against privileged access. Keep `AAR_DATA_DIR` on the executor's
private native filesystem. Windows uses the existing WSL distribution.

The journal, backups, and policy are outside the repository and release archives.
They are never uploaded as part of this project. To remove personal state,
disable the plugin, preserve any backups you still need, and remove that state
directory yourself. Removing it also removes rollback history.

If reporting a defect, share a minimal sanitized reproduction through
[GitHub Issues](https://github.com/dev66613/after-action-review/issues).
