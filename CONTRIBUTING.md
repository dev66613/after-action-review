# Contributing

Start with a reproducible issue or a small pull request. Explain the observed
behavior, expected behavior, execution host, and Codex/Python versions. Remove
personal paths, credentials, transcript contents, and private journal records
from examples.

The runtime uses Python 3.11+ and the standard library. From the repository root:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/package.py --output dist
```

Use `python` on Windows. Run the transaction suite inside Linux/WSL, where its
POSIX filesystem guarantees apply. Tests use isolated temporary state.

Keep `plugin.json`, `.codex-plugin/plugin.json`, and `aar/__init__.py` versions
in sync. Preserve the existing default starter prompt. Update both MCP manifests
when launch configuration changes; verify discovery through the actual host.

Document behavior changes in [CHANGELOG.md](CHANGELOG.md). Add regression tests
for consequential runtime changes. Documentation and visual edits need focused
validation rather than tests that duplicate their implementation.

Changes to permissions, write scope, hook execution, evidence requirements,
journaling, or rollback need explicit discussion and behavior-focused evidence.
Keep native hook trust and approval controls intact. Never publish runtime state
or modify installed plugin caches by hand.

See [architecture](docs/architecture.md) and [compatibility](docs/compatibility.md)
before changing lifecycle or platform behavior.
