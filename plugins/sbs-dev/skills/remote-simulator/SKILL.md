---
name: remote-simulator
description: Stream an iOS Simulator from a Mac to a phone browser, manage concurrent sessions across projects, and clean up managed simulator resources. Use when the user asks for a remote simulator link, phone interaction, session status, or simulator streaming cleanup.
---

# Remote simulator

Use the bundled Python 3.8+ manager at `scripts/remote_simulator.py`. Resolve this path relative to this skill directory. Invoke it with `python3`; commands return structured session details. Run `--help` or a subcommand’s `--help` for supported options.

The phone opens the returned link in Safari. Do not promise an embedded stream in ChatGPT iOS. This streams the Mac’s iOS Simulator, so the Mac must stay awake and reachable.

## Setup

Check `status` first. Setup is required before the first `start`: it installs the pinned `serve-sim` dependency and a per-user macOS LaunchAgent outside project repositories, shared by all projects for this user. Prerequisites are Xcode with an installed iOS Simulator runtime, Python 3.8+, Node.js/npm, and Tailscale on both the Mac and phone on the same tailnet. Tailscale must be running and Tailscale Serve available for this machine.

```sh
python3 scripts/remote_simulator.py setup
python3 scripts/remote_simulator.py status
```

Optional setup flags are `--max-sessions N`, `--idle-seconds N`, `--max-age N` (seconds), and `--retention-days N`. Defaults are two sessions, 600 idle seconds, 28,800 maximum-age seconds, and seven retention days.

Execute setup when needed for the user’s requested streaming workflow and permitted by the repository’s instructions. Installing or editing the plugin alone does not call setup. Default sharing uses private Tailscale HTTPS; do not substitute a public tunnel. `start --local` is available for local smoke tests and does not provide remote phone access.

## Start and use a session

Read the target repository’s build and simulator instructions first. Use its existing app project, build scripts, and launch tools. Resolve the project or worktree path before starting; the manager tracks its real path.

Choose a template device with the desired runtime and device type, then start a managed session:

```sh
python3 scripts/remote_simulator.py start --project /path/to/project --template TEMPLATE_UDID
```

The manager creates a fresh simulator using the template’s runtime and device type, without copying its apps or data. The template can be booted. Later sessions can reuse an idle managed device belonging to the same project and template.

Start **before building**, then use the returned simulator UDID for the build destination, installation, and launch. Distinct sessions reserve distinct devices and ports. Do not target the template or a generic booted simulator. Do not copy or change the project to make streaming work.

To stream an existing simulator, use `--device UDID` instead of `--template`. The device must already be booted. This borrows the device exclusively for the session. Borrowed devices are never shut down by cleanup. Prefer managed devices when automatic simulator shutdown is desired.

Protect builds, tests, and other agent operations with `run`, which renews an operation lease while the command runs:

```sh
python3 scripts/remote_simulator.py run --session SESSION_ID -- /path/to/project/build-script --device SESSION_UDID
```

For Swift/Xcode build or test commands, preserve the project’s required formatter and pipeline failure handling. In this plugin, all such output must pass through `xcsift`.

For operations performed through tools rather than a shell command, acquire a bounded lease and release its returned token when finished:

```sh
python3 scripts/remote_simulator.py hold --session SESSION_ID --seconds 300
python3 scripts/remote_simulator.py release --session SESSION_ID --lease LEASE_TOKEN
```

Renew with another bounded hold if needed, releasing unused tokens. Do not create long leases just to keep an unattended session alive. An operation lease cannot extend the session’s absolute maximum age.

Return the session’s clickable `url` and identify the project and simulator. Preserve the complete URL, including its fragment: that fragment contains the browser access token. Treat it as private access information and do not include it in committed files or logs. Opening the page supplies the viewer heartbeat; no keepalive from the Codex conversation is required.

The mobile viewer exposes the video stream, gestures, Home, and rotation. It does not expose the upstream shell or simulator-grid tools. Report any unsupported interaction instead of promising all upstream features.

## Lifecycle and cleanup

- The default limit is two concurrent sessions. At capacity, inspect `status` and stop an unneeded session only when the user’s intent authorizes it; do not evict another active project.
- Viewer heartbeats count as live for at most 45 seconds. Backgrounded or disconnected browsers eventually stop keeping the session alive. A ten-minute idle window permits reconnecting; active operation leases also keep the session alive.
- Sessions have an eight-hour maximum lifetime by default, adjustable at start with `--max-age SECONDS`. The watchdog checks every thirty seconds; expired sessions are cleaned up on a subsequent sweep, plus the time needed to stop processes and shut down the device.
- Cleanup stops the session’s stream, gateway, and Tailscale route, then shuts down its managed simulator. It does not shut down borrowed devices or unrelated simulators. Inactive managed devices are automatically pruned after seven days by default.
- Explicitly stopping a session ends all of its viewer and operation leases; a held operation does not prevent an explicit stop.
- The per-user LaunchAgent performs cleanup after Codex exits or session processes crash. A stopped/sleeping Mac cannot serve video or run the watchdog until it resumes.

```sh
python3 scripts/remote_simulator.py status
python3 scripts/remote_simulator.py stop --session SESSION_ID
python3 scripts/remote_simulator.py prune --days 7
```

Use `stop` when the user finishes or asks to reclaim resources. `prune` deletes inactive manager-owned devices older than the requested retention period; use it when the user requests old-device cleanup. Never use broad `simctl shutdown all`, process-name kills, or unrelated Tailscale resets for session cleanup.

Report validation honestly: local HTTP checks or a Simulator screenshot do not prove that phone video, touch input, or cellular connectivity work. Verify those separately when available.
