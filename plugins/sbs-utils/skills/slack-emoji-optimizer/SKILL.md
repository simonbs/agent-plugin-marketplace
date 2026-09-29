---
name: slack-emoji-optimizer
description: Convert animated WebP images into high-quality Slack custom emoji GIFs while preserving animation and transparency. Use when a user asks to make, optimize, compress, or fix an animated WebP for Slack emoji upload. Do not use for ordinary message GIF attachments or non-WebP sources.
---

# Slack Emoji Optimizer

Create a non-destructive, upload-ready sibling GIF from an animated WebP with `scripts/optimize_slack_emoji.py`, resolving that path relative to this `SKILL.md` rather than the user's working directory. The helper targets the conservative Slack envelope used by this skill: a 128×128 canvas, fewer than 128,000 bytes, no more than 50 frames, and an infinite animation loop.

```bash
python3 scripts/optimize_slack_emoji.py /path/to/input.webp
```

Pass several inputs in one invocation when requested. Use `--output` only with one input when the user names a destination. The helper chooses a new `-slack.gif`, `-slack-v2.gif`, and so on unless `--force` explicitly authorizes replacement.

## Required behavior

- Preserve the subject, timing, aspect ratio, animation, and existing transparency. Do not use generative image editing for this deterministic conversion.
- Never let ImageMagick decode an animated WebP. Its coalescing can fill the WebP canvas background and create black or white rectangles. The helper deliberately uses FFmpeg for decoding, premultiplied-alpha Lanczos scaling, frame sampling, and palette mapping; ImageMagick only performs lossless/delta optimization on the intermediate GIF.
- Prefer visual fidelity over excess size margin. Keep as many frames and palette colors as possible, then use modest inter-frame fuzz before reducing colors or frame count.
- Do not invent transparency for an intentionally opaque source. When the source has transparency, verify the output still has transparent pixels and inspect representative frames composited over both a light and a dark background.
- Keep the source untouched. Do not delete an earlier conversion unless the user asks.

## Verification

Treat the helper's successful exit and final summary as the mechanical validation. Also visually inspect the first, midpoint, and final coalesced frames when transparency, edge quality, or a previous failed conversion matters. Coalesce an optimized GIF before extracting a non-first frame; otherwise the preview may show only a delta rectangle.

```bash
tmp_dir=$(mktemp -d)
magick output-slack.gif -coalesce "$tmp_dir/frame-%03d.png"
# Repeat these two commands for frame 000, the midpoint, and the final frame.
selected_frame="$tmp_dir/frame-000.png"
magick "$selected_frame" -background white -alpha background "$tmp_dir/light.png"
magick "$selected_frame" -background '#1a1d21' -alpha background "$tmp_dir/dark.png"
```

Report the output path, byte size, dimensions, and frame count. If the helper cannot fit the image without going below its minimum quality floor, say so and ask whether the user prefers fewer frames or a smaller canvas rather than silently producing an ugly result.

## Dependencies

The helper requires `ffmpeg`, `ffprobe`, and ImageMagick's `magick` on `PATH`. If any are missing, report the missing executable rather than substituting a pipeline that risks losing transparency.
