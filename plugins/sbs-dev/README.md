# sbs-dev

Developer skills for remote iOS simulators, pull request feedback, and Swift build diagnostics.

See the [marketplace README](../../README.md) for installation, updates, and removal.

## Skills

| Skill | Description |
| --- | --- |
| [banzai-pr-feedback-loop](skills/banzai-pr-feedback-loop/SKILL.md) | Monitor pull requests for Banzai Codes feedback and address blockers until the current commit is clean. |
| [remote-simulator](skills/remote-simulator/SKILL.md) | Stream isolated iOS Simulator sessions to a phone with private URLs, operation leases, and automatic cleanup. |
| [xcsift](skills/xcsift/SKILL.md) | Parse Swift and Xcode build output into structured diagnostics, test results, and timing information. |

## Remote simulators

Ask the agent to “Run this app and stream the simulator to my phone.” The Mac runs the app; you open the returned link in Safari while continuing the conversation in ChatGPT. Embedding the stream inside the ChatGPT iOS app is not guaranteed.

Requires macOS, Xcode with an installed iOS Simulator runtime, Python 3.8+, Node.js/npm, and Tailscale on the Mac and phone signed into the same tailnet. Before first use, run the skill’s `setup` command to install a pinned `serve-sim` dependency and a per-user LaunchAgent outside project repositories. Tailscale Serve supplies private HTTPS access; no public tunnel is created. Installing this plugin does not perform setup.

Sessions reserve separate simulator devices and ports across projects and worktrees. Managed devices use the template’s device type and runtime without copying its app data, and idle managed devices can be reused. By default, up to two sessions run at once. A session expires after ten minutes without a viewer or operation lease, or after eight hours overall. The LaunchAgent checks every thirty seconds, including after the Codex conversation ends. Managed devices shut down on cleanup; borrowed devices remain running. Inactive managed devices are automatically deleted after seven days.

The mobile viewer supports streaming, gestures, Home, and rotation. It does not expose the upstream shell or grid tools. Setup flags configure session capacity, idle timeout, maximum age, and device retention.

See the [skill instructions](skills/remote-simulator/SKILL.md) for setup and session commands. Use `--local` only for local development checks; it does not provide phone access.

Session links contain temporary bearer tokens and should stay private. Runtime state, logs, and dependencies are stored in the user's Application Support directory outside project repositories; do not copy them into the plugin or public bug reports.

For development, run the manager’s isolated test suite from the marketplace checkout:

```sh
python3 -m unittest discover -s plugins/sbs-dev/skills/remote-simulator/tests -v
node --test plugins/sbs-dev/skills/remote-simulator/tests/viewer.test.mjs
```

Verified on macOS with two simultaneous managed simulators, real video frames, native input, crash recovery, lifetime expiry, and cleanup preserving an existing booted simulator. Private Tailscale HTTPS and browser tap forwarding were also checked; the temporary route was removed without changing existing services. The installed LaunchAgent completed a real sweep successfully; its launcher and user environment are also covered by the isolated tests. The owner confirmed that the local trial works. Cellular connectivity has not been separately verified.
