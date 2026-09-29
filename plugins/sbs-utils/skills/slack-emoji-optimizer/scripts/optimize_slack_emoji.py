#!/usr/bin/env python3
"""Create high-quality Slack custom emoji GIFs without losing transparency."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path


PALETTES = (256, 224, 192, 160, 128, 112, 96, 80, 64, 48, 32)
FUZZ_LEVELS = (0.0, 0.5, 1.0, 1.5, 2.1)


class OptimizationError(RuntimeError):
    pass


def run(command: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "command failed"
        raise OptimizationError(f"{command[0]}: {detail}")
    return result.stdout if capture else ""


def require_tools() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe", "magick") if not shutil.which(name)]
    if missing:
        raise OptimizationError("missing required executable(s): " + ", ".join(missing))


def parse_fraction(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None


def probe(path: Path) -> dict[str, float | int | None]:
    raw = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,duration,avg_frame_rate,r_frame_rate,nb_read_frames:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture=True,
    )
    data = json.loads(raw)
    streams = data.get("streams", [])
    if not streams:
        raise OptimizationError(f"no video/image stream found in {path}")
    stream = streams[0]
    average_rate = parse_fraction(stream.get("avg_frame_rate"))
    nominal_rate = parse_fraction(stream.get("r_frame_rate"))
    frame_rate = average_rate or (
        nominal_rate if nominal_rate is not None and nominal_rate <= 240 else None
    )
    frame_text = stream.get("nb_read_frames")
    frame_count = int(frame_text) if frame_text and frame_text != "N/A" else None
    duration = None
    for candidate in (stream.get("duration"), data.get("format", {}).get("duration")):
        try:
            if candidate and candidate != "N/A" and float(candidate) > 0:
                duration = float(candidate)
                break
        except ValueError:
            pass
    if duration is None:
        packet_times = run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "packet=duration_time",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture=True,
        )
        values = []
        for line in packet_times.splitlines():
            try:
                values.append(float(line.strip().split(",")[0]))
            except (ValueError, IndexError):
                continue
        if values and sum(values) > 0:
            duration = sum(values)
    if duration is None and frame_count and frame_rate:
        duration = frame_count / frame_rate
    if duration and frame_count:
        frame_rate = frame_count / duration
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "frames": frame_count,
        "fps": frame_rate,
        "duration": duration,
    }


def unique_output(input_path: Path, requested: Path | None) -> Path:
    if requested:
        return requested.expanduser().resolve()
    base = input_path.with_name(f"{input_path.stem}-slack.gif")
    if not base.exists():
        return base
    version = 2
    while True:
        candidate = input_path.with_name(f"{input_path.stem}-slack-v{version}.gif")
        if not candidate.exists():
            return candidate
        version += 1


def gif_frame_count(path: Path) -> int:
    output = run(
        ["magick", "identify", "-format", "%n\n", str(path)], capture=True
    ).strip()
    return int(output.splitlines()[0])


def gif_dimensions(path: Path) -> tuple[int, int]:
    output = run(
        ["magick", "identify", "-format", "%W %H", f"{path}[0]"], capture=True
    ).strip()
    width, height = output.split()
    return int(width), int(height)


def make_raw(
    input_path: Path,
    output_path: Path,
    *,
    fps: float,
    colors: int,
    canvas: int,
) -> None:
    filter_graph = (
        f"[0:v]format=rgba,premultiply=inplace=1,"
        f"scale={canvas}:{canvas}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"unpremultiply=inplace=1,fps={fps:.12g},split[a][b];"
        f"[a]palettegen=max_colors={colors}:reserve_transparent=1:stats_mode=diff[p];"
        f"[b][p]paletteuse=dither=none:alpha_threshold=128:diff_mode=rectangle"
    )
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-filter_complex",
            filter_graph,
            "-loop",
            "0",
            str(output_path),
        ]
    )


def optimize_gif(raw: Path, output: Path, fuzz: float, canvas: int) -> None:
    command = [
        "magick",
        str(raw),
        "-coalesce",
        "-alpha",
        "set",
        "-background",
        "none",
        "-gravity",
        "center",
        "-extent",
        f"{canvas}x{canvas}",
    ]
    if fuzz:
        command.extend(["-fuzz", f"{fuzz}%"])
    command.extend(["-layers", "OptimizeTransparency"])
    if gif_dimensions(raw) == (canvas, canvas):
        command.extend(["-layers", "Optimize"])
    command.extend(["-loop", "0", str(output)])
    run(command)


def alpha_summary(path: Path) -> tuple[int, int]:
    values = run(
        [
            "magick",
            str(path),
            "-coalesce",
            "-format",
            "%[opaque] %[pixel:p{0,0}]\n",
            "info:",
        ],
        capture=True,
    ).splitlines()
    transparent = sum(1 for line in values if line.split(maxsplit=1)[0] == "False")
    corner_transparent = sum(
        1 for line in values if "srgba(" in line and line.rstrip().endswith(",0)")
    )
    return transparent, corner_transparent


def optimize_one(
    input_path: Path,
    output_path: Path,
    *,
    max_bytes: int,
    max_frames: int,
    canvas: int,
    force: bool,
) -> dict[str, object]:
    if not input_path.is_file():
        raise OptimizationError(f"input does not exist: {input_path}")
    if input_path.suffix.lower() != ".webp":
        raise OptimizationError(
            f"unsupported input format for this transparency-safe workflow: {input_path.suffix or '(none)'}; "
            "expected an animated WebP"
        )
    if output_path.exists() and not force:
        raise OptimizationError(f"output exists (use --force to replace it): {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = probe(input_path)
    duration = metadata["duration"]
    source_frames = metadata["frames"]
    source_fps = metadata["fps"]
    if duration and duration > 0:
        desired_frames = min(max_frames, source_frames or max_frames)
        fps = desired_frames / duration
        if source_fps:
            fps = min(fps, source_fps)
    else:
        fps = min(source_fps or 1.0, float(max_frames))

    with tempfile.TemporaryDirectory(prefix="slack-emoji-") as tmp_name:
        tmp = Path(tmp_name)
        selected: tuple[Path, int, float, int] | None = None
        target_frames = min(max_frames, source_frames or max_frames)
        minimum_frames = min(target_frames, max(1, math.ceil(target_frames * 0.6)))

        while target_frames >= minimum_frames and selected is None:
            if duration and duration > 0:
                fps = target_frames / duration
            for colors in PALETTES:
                raw = tmp / f"raw-f{target_frames}-c{colors}.gif"
                make_raw(input_path, raw, fps=fps, colors=colors, canvas=canvas)
                actual_frames = gif_frame_count(raw)
                if actual_frames > max_frames:
                    continue
                fuzzes = FUZZ_LEVELS if colors == 256 else (0.0,)
                for fuzz in fuzzes:
                    candidate = tmp / f"candidate-f{target_frames}-c{colors}-z{fuzz}.gif"
                    optimize_gif(raw, candidate, fuzz, canvas)
                    if candidate.stat().st_size < max_bytes:
                        selected = (candidate, colors, fuzz, actual_frames)
                        break
                if selected:
                    break
            if selected is None:
                # Try modest fuzz with reduced palettes before sacrificing motion.
                for colors in PALETTES[1:]:
                    raw = tmp / f"raw-f{target_frames}-c{colors}.gif"
                    for fuzz in (1.0, 2.1):
                        candidate = tmp / f"candidate-f{target_frames}-c{colors}-z{fuzz}.gif"
                        optimize_gif(raw, candidate, fuzz, canvas)
                        if candidate.stat().st_size < max_bytes:
                            selected = (candidate, colors, fuzz, gif_frame_count(candidate))
                            break
                    if selected:
                        break
            if selected is None:
                target_frames -= max(1, math.ceil(target_frames * 0.05))

        if selected is None:
            raise OptimizationError(
                f"could not fit {input_path.name} below {max_bytes} bytes without dropping "
                f"below the {minimum_frames}-frame quality floor"
            )

        candidate, colors, fuzz, frames = selected
        if frames > max_frames:
            raise OptimizationError(f"internal validation failed: {frames} frames")
        if candidate.stat().st_size >= max_bytes:
            raise OptimizationError("internal validation failed: output is too large")
        shutil.copyfile(candidate, output_path)

    transparent_frames, transparent_corners = alpha_summary(output_path)
    return {
        "input": str(input_path),
        "output": str(output_path),
        "bytes": output_path.stat().st_size,
        "width": canvas,
        "height": canvas,
        "frames": gif_frame_count(output_path),
        "palette_colors": colors,
        "fuzz_percent": fuzz,
        "transparent_frames": transparent_frames,
        "transparent_corner_frames": transparent_corners,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert animated WebPs to high-quality Slack custom emoji GIFs."
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, help="output path (single input only)")
    parser.add_argument("--max-bytes", type=int, default=127_000)
    parser.add_argument("--max-frames", type=int, default=50)
    parser.add_argument("--canvas", type=int, default=128)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.output and len(args.inputs) != 1:
        parser.error("--output can only be used with one input")
    if args.max_bytes < 1 or args.max_frames < 1 or args.canvas < 1:
        parser.error("limits must be positive")

    try:
        require_tools()
        results = []
        for input_arg in args.inputs:
            input_path = input_arg.expanduser().resolve()
            requested = args.output.expanduser().resolve() if args.output else None
            output_path = unique_output(input_path, requested)
            results.append(
                optimize_one(
                    input_path,
                    output_path,
                    max_bytes=args.max_bytes,
                    max_frames=args.max_frames,
                    canvas=args.canvas,
                    force=args.force,
                )
            )
    except (OptimizationError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for result in results:
            print(
                f"{result['output']}: {result['bytes']} bytes, "
                f"{result['width']}x{result['height']}, {result['frames']} frames, "
                f"palette={result['palette_colors']}, fuzz={result['fuzz_percent']}%, "
                f"transparent_frames={result['transparent_frames']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
