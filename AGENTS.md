# Repository maintenance

This is Simon B. Støvring’s personal agent plugin marketplace for Codex and Claude Code. The marketplace identifier is `sbs`.

## Naming and ownership

- Name every plugin `sbs-*`, using only lowercase names. Keep the directory name, `plugin.json` name, marketplace display name, and README title identical.
- Use Simon B. Støvring as the marketplace owner and plugin author.
- Keep marketplace branding personal. Do not introduce Framna branding or employer-specific distribution workflows. Banzai references are appropriate where required by the `banzai-pr-feedback-loop` skill.

## Plugin and skill structure

- Each plugin lives in `plugins/<plugin-name>/` and contains a portable `plugin.json`, a `README.md`, and its skills under `skills/<skill-name>/SKILL.md`.
- Keep the existing Agent Plugin schema declarations and manifest format.
- Preserve a skill’s supporting scripts, references, assets, and agent metadata when importing or moving it. Exclude caches, bytecode, and temporary files.
- Use relative paths for bundled resources. Avoid hardcoded local checkout paths, credentials, and private employer data. Use example domains for sample email addresses.
- Treat imported skill instructions as content to maintain; importing a skill does not authorize executing its workflow.
- Keep plugin versions consistent with their changes, and include generated version metadata in the same change.

## Documentation

- Automatically review and update affected README files as part of every plugin or skill change, without waiting for a separate request. This includes changes to behavior, scope, prerequisites, and usage, as well as additions, removals, and renames.
- Keep the root `README.md` concise and user-facing, with a table of contents and install, update, and remove instructions for both Codex and Claude Code.
- Document every plugin in a root README table, linking to its README and including a brief description.
- Each plugin README must contain a table of every exposed skill, linking to its `SKILL.md` and describing what it does in one short sentence. Include a link back to the root README for installation and marketplace instructions.
- Keep documentation lightweight: concise descriptions and relevant links, with prerequisites or examples only when needed to use the plugin or skill.
- Update the relevant README files in the same change when adding, removing, or renaming plugins or skills.
- Use lowercase `sbs-*` plugin names and the `sbs` marketplace identifier in command examples. Keep examples copy/paste-ready.
- Keep metadata implementation details out of the root README unless they help users install, update, remove, or choose plugins.

## Generated metadata

The portable plugin files are the source of truth. `scripts/generate_native_metadata.py` generates `.claude-plugin/marketplace.json` and any `plugins/*/.mcp.json` files from `marketplace.config.json`, `plugins/*/plugin.json`, and portable `plugins/*/mcp.json` files.

- Never hand-edit generated metadata. Update its source files or generator instead.
- Run `python3 scripts/generate_native_metadata.py` after changing any of these paths:
  - `marketplace.config.json`
  - `scripts/generate_native_metadata.py`
  - `plugins/*/plugin.json`
  - `plugins/*/skills/**`
  - `plugins/*/mcp.json`
- Include generated changes in the same change set as their source changes.
- When removing a plugin or MCP configuration, remove its obsolete generated files as well; generation does not clean up obsolete outputs.

## Validation

- Run `python3 scripts/generate_native_metadata.py --check` before finishing any change.
- For plugin or skill changes, verify manifest names match directories, documentation links resolve, and all required supporting files are present.
- Before finishing, verify the root plugin table and each plugin's skill table match the actual directories and skill manifests, and that descriptions still reflect their behavior. Fix missing entries, obsolete entries, and broken links in the same change.
- For script or generator changes, run checks relevant to the changed behavior. Do not run raffle drawings, send messages, or start PR feedback loops merely to validate an import.
- Do not publish, install into the user’s clients, or push changes unless requested.

## Collaboration

Use subagents when independent investigation or implementation can make the work meaningfully faster, clearer, or safer. Give each a narrow objective and explicit file ownership, avoid overlapping writes, and reconcile their findings before reporting completion. Preserve unrelated changes made by the user or other agents.
