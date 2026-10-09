# Compatibility and verified coverage

Requirements: Python 3.11+ and a Codex execution host with plugin hooks.
There are no Python package dependencies or plugin-specific API keys.

| Execution host | Setup |
| --- | --- |
| Linux or WSL Codex | Use `scripts/install.py --host linux --install` in that executor |
| Native Windows Codex | Use `--host windows --install`; Python forwards runtime calls to an existing Ubuntu WSL distribution |
| Another WSL distribution | Set `AAR_WSL_DISTRO` to its existing name; install Python 3.11+ there |
| Server-hosted Codex | Install the package, register that server's owned paths, and review its hooks locally |
| Standalone ChatGPT cloud chat | Needs a separately connected execution host or authenticated hosted MCP; this package does not provision one |

The supplied installer defaults to the Codex compatibility layout. It preserves
the portable manifest at `metadata/portable-plugin.json` and leaves
`.codex-plugin/plugin.json` as the native entry point. On the tested Windows
Codex 0.162.0, a root portable manifest prevented hook discovery. Source keeps
the portable layout; `--format portable` opts into it when your client supports
it. Recheck behavior after upgrades. See the
[upstream issue](https://github.com/openai/codex/issues/39895).

Both MCP manifests use `cwd: "./"` and `scripts/aar.py`. Codex 0.160.1 did not
expand plugin-root placeholders in MCP arguments on the tested host. Packaged
launch is covered by a regression test from an unrelated directory; native
discovery was also checked.

Before this publication, verification covered:

- 57 automated tests for transaction behavior, recovery, scope, privacy, event
  handling, feedback, and packaged MCP launch.
- A real Windows-to-WSL launch, eight discovered MCP tools, all seven handlers,
  private lesson loading, scoped apply, and guarded rollback.
- A fresh native WSL session completing a task, recording one AAR, and asking
  one optional survey with one Stop continuation and no recursion.

The 0.1.2 release adds branding, documentation, and packaging; it does not change
the review or transaction engine. The release checks are described in the
[changelog](../CHANGELOG.md) and release notes.

Hook trust belongs to the actual execution host. Updating a package can require
reviewing the current definitions again in `/hooks`. Use separate host profiles
for Windows and WSL when necessary: a shared Codex home may store one trust hash
for a handler even though its effective platform commands differ. Do not forge
trust or disable approvals to resolve this.

Reviews happen while the agent is active. There is no idle-session timer, and
interrupt/end events wait for the next authorized continuation. Some specialized
execution paths may omit `PostToolUse`; the agent must identify semantic blockers
itself. Missing earlier context limits what a review can conclude.

Distribution is through GitHub and manual local installation. Lifecycle hooks
are currently ineligible for the public OpenAI plugin directory according to
[official packaging guidance](https://developers.openai.com/plugins/build/plugins).
GitHub publication does not make the package an official OpenAI plugin.
