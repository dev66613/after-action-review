---
name: setup
description: Configure owned write scope and survey preferences for After Action Review, verify its runtime, and explain the host's required hook trust step.
---

# Configure After Action Review

Inspect status/doctor with scripts/aar.py at the plugin root. Derive that root
from this skill's location. Initial auto scope is private lessons; register
additional user-owned skill/prompt/workflow directories when the user requests
that scope. Do not register managed caches or synced references. Configuration
preserves a previous policy copy and never changes native approvals.

Use scripts/install.py for requested local installation. It detects Windows/WSL
versus Linux and uses the target host's native Codex marketplace/install commands.
It never edits plugin caches or forges hook trust. The host requires the human to
review/trust installed hook definitions through /hooks; the plugin cannot perform
that review on the user's behalf.

Verify discovery, harmless aar_status, an event and change/rollback in an isolated
directory. Report version/checks and untested fresh-session behavior. Files on disk
do not prove hooks are trusted or that an existing chat has reloaded them.

Install this Python package on each requested server execution host and register
that host's paths. ChatGPT Cloud needs a separately connected execution host or
authenticated MCP connection. Local installation does not monitor unconnected
cloud chats. Deployment/endpoint exposure/credentials require a concrete explicit
request; do not invent endpoints or claim a local process is hosted.
