# 🧩 Agent Plugin Marketplace

Personal agent plugins for Codex and Claude Code. The marketplace name is `sbs`.

## 📚 Table of contents

- [🧩 Plugins](#plugins)
- [📦 Install](#install)
- [🔄 Update](#update)
- [🗑️ Remove](#remove)

<a id="plugins"></a>

## 🧩 Plugins

| Plugin | Description |
| --- | --- |
| [sbs-dev](plugins/sbs-dev/README.md) | Stream iOS simulators to your phone, address pull request review blockers, and parse Swift and Xcode build output. |
| [sbs-utils](plugins/sbs-utils/README.md) | Draw raffle winners from CSV files and convert animated WebP images into Slack emoji GIFs. |
| [sbs-news](plugins/sbs-news/README.md) | Find Danish news candidates for Langt fra løgnen’s sidelæns Pulitzer and draft listener submissions. |

<a id="install"></a>

## 📦 Install

### Codex

```sh
codex plugin marketplace add simonbs/agent-plugin-marketplace --ref main
codex plugin add sbs-dev@sbs
codex plugin add sbs-utils@sbs
codex plugin add sbs-news@sbs
```

### Claude Code

```sh
claude plugin marketplace add simonbs/agent-plugin-marketplace
claude plugin install sbs-dev@sbs
claude plugin install sbs-utils@sbs
claude plugin install sbs-news@sbs
```

Install only the plugins you want. To use a local checkout, replace the marketplace add command above with `codex plugin marketplace add /path/to/agent-plugin-marketplace` or `claude plugin marketplace add /path/to/agent-plugin-marketplace`.

<a id="update"></a>

## 🔄 Update

### Codex

Refresh the marketplace, then reinstall the plugins you use:

```sh
codex plugin marketplace upgrade sbs
codex plugin add sbs-dev@sbs
codex plugin add sbs-utils@sbs
codex plugin add sbs-news@sbs
```

### Claude Code

```sh
claude plugin marketplace update sbs
claude plugin update sbs-dev@sbs
claude plugin update sbs-utils@sbs
claude plugin update sbs-news@sbs
```

Restart Claude Code after updating to load the new plugin versions.

<a id="remove"></a>

## 🗑️ Remove

### Codex

```sh
codex plugin remove sbs-dev@sbs
codex plugin remove sbs-utils@sbs
codex plugin remove sbs-news@sbs
codex plugin marketplace remove sbs
```

### Claude Code

```sh
claude plugin uninstall sbs-dev@sbs
claude plugin uninstall sbs-utils@sbs
claude plugin uninstall sbs-news@sbs
claude plugin marketplace remove sbs
```

Run only the removal commands for plugins you installed. Both clients also offer `/plugins` for interactive plugin management.
