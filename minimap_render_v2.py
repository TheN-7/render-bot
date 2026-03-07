#!/usr/bin/env python3
"""Compatibility entrypoint for minimap rendering.

This wrapper preserves prior CLI flags while using the canonical replay pipeline.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

from core.minimap_data import load_canonical_data, canonical_to_legacy
from renderers.minimap_renderer import estimate_animation_frame_count, iter_animation_frames, render_static, render_gif_frames


ProgressCallback = Callable[[str, int, int], None]
PLAYBACK_DURATION_SCALE = 1.45
MP4_CRF = "17"
MP4_PRESET = "slow"
AUTO_OUTPUT_MIN_S = 40.0
AUTO_OUTPUT_MAX_S = 60.0
AUTO_BATTLE_MAX_S = 1200.0


def _battle_duration_seconds(canonical: Dict[str, Any]) -> float:
    battle_end = float(canonical.get("stats", {}).get("battle_end_s", 0.0))
    if battle_end > 0:
        return battle_end
    tracks = canonical.get("tracks", {}) or {}
    return max(
        (float(p.get("t", 0.0)) for t in tracks.values() for p in (t.get("points", []) or [])),
        default=0.0,
    )


def _resolve_speed(canonical: Dict[str, Any], fps: int, speed: float, target_duration_s: float | None) -> float:
    if not target_duration_s or target_duration_s <= 0:
        return max(0.05, float(speed) / PLAYBACK_DURATION_SCALE)
    battle_seconds = _battle_duration_seconds(canonical)
    if battle_seconds <= 0:
        return max(0.05, float(speed) / PLAYBACK_DURATION_SCALE)
    effective_duration_s = float(target_duration_s) * PLAYBACK_DURATION_SCALE
    target_frames = max(2, int(round(effective_duration_s * max(1, int(fps)))))
    return max(0.05, battle_seconds / float(max(1, target_frames - 1)))


def auto_output_duration_s(
    canonical: Dict[str, Any],
    min_output_s: float = AUTO_OUTPUT_MIN_S,
    max_output_s: float = AUTO_OUTPUT_MAX_S,
    max_battle_s: float = AUTO_BATTLE_MAX_S,
) -> float:
    battle_seconds = _battle_duration_seconds(canonical)
    min_output_s = float(min_output_s)
    max_output_s = max(min_output_s, float(max_output_s))
    max_battle_s = max(1.0, float(max_battle_s))
    ratio = max(0.0, min(1.0, battle_seconds / max_battle_s))
    return min_output_s + (max_output_s - min_output_s) * ratio


def internal_target_duration_s(output_duration_s: float) -> float:
    return max(0.05, float(output_duration_s) / PLAYBACK_DURATION_SCALE)


def _save_mp4(frames, out_mp4: str, fps: int, progress: ProgressCallback | None = None, total_frames: int | None = None) -> None:
    iterator = iter(frames)
    try:
        first_frame = next(iterator)
    except StopIteration as exc:
        raise RuntimeError("No frames were generated for MP4 export") from exc

    frame_size = first_frame.size
    fps = max(1, int(fps))
    total = max(1, int(total_frames or 0))
    written = 0

    def _emit(stage: str, current: int, total_count: int) -> None:
        if progress is not None:
            progress(stage, int(current), max(1, int(total_count)))

    # Preferred path: imageio writer with higher-quality H.264 settings.
    try:
        import numpy as np
        import imageio.v2 as imageio
    except Exception:
        np = None
        imageio = None

    if imageio is not None and np is not None:
        writer = imageio.get_writer(
            out_mp4,
            fps=fps,
            codec="libx264",
            macro_block_size=None,
            pixelformat="yuv420p",
            output_params=["-crf", MP4_CRF, "-preset", MP4_PRESET, "-movflags", "+faststart"],
        )
        try:
            _emit("encoding", 0, total)
            writer.append_data(np.array(first_frame.convert("RGB")))
            written = 1
            _emit("encoding", written, total)
            for frame in iterator:
                writer.append_data(np.array(frame.convert("RGB")))
                written += 1
                _emit("encoding", written, total)
        finally:
            writer.close()
        return

    # Fallback: ffmpeg raw-frame pipe to preserve full frame quality.
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("MP4 export requires imageio+numpy or ffmpeg in PATH")

    process = subprocess.Popen(
        [
            ffmpeg,
            "-y",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{frame_size[0]}x{frame_size[1]}",
            "-r",
            str(fps),
            "-i",
            "-",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            MP4_CRF,
            "-preset",
            MP4_PRESET,
            "-movflags",
            "+faststart",
            "-pix_fmt",
            "yuv420p",
            out_mp4,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _emit("encoding", 0, total)
        assert process.stdin is not None
        process.stdin.write(first_frame.convert("RGB").tobytes())
        written = 1
        _emit("encoding", written, total)
        for frame in iterator:
            process.stdin.write(frame.convert("RGB").tobytes())
            written += 1
            _emit("encoding", written, total)
        process.stdin.close()
        _, stderr = process.communicate()
    finally:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace") or "ffmpeg MP4 export failed")


def render_minimap(
    replay_path: str,
    *,
    canonical: Dict[str, Any] | None = None,
    out_mp4: str | None = None,
    out_png: str | None = None,
    out_gif: str | None = None,
    dump_json: str | None = None,
    dump_legacy_json: str | None = None,
    size: int = 1024,
    fps: int = 25,
    speed: float = 3.0,
    target_duration_s: float | None = None,
    show_labels: bool = True,
    show_grid: bool = True,
    bg_color: Tuple[int, int, int] = (10, 20, 40),
    progress: ProgressCallback | None = None,
) -> Dict[str, Any]:
    src = Path(replay_path)
    if not src.is_file():
        raise FileNotFoundError(f"file not found: {replay_path}")

    if canonical is None:
        if progress is not None:
            progress("loading", 0, 1)
        canonical = load_canonical_data(str(src))
        if progress is not None:
            progress("loading", 1, 1)

    if dump_json:
        with open(dump_json, "w", encoding="utf-8") as f:
            json.dump(canonical, f, indent=2)

    if dump_legacy_json:
        legacy = canonical_to_legacy(canonical)
        with open(dump_legacy_json, "w", encoding="utf-8") as f:
            json.dump(legacy, f, indent=2)

    speed = _resolve_speed(canonical, fps, speed, target_duration_s)
    base = os.path.splitext(str(src))[0]
    mp4_path = out_mp4 or (base + "_minimap.mp4")

    total_frames = estimate_animation_frame_count(canonical, speed=speed)
    if progress is not None:
        progress("rendering", 0, total_frames)
    mp4_frames = iter_animation_frames(
        canonical,
        canvas_size=size,
        speed=speed,
        show_grid=show_grid,
    )
    _save_mp4(mp4_frames, mp4_path, fps, progress=progress, total_frames=total_frames)
    if progress is not None:
        progress("done", total_frames, total_frames)

    if out_png:
        img = render_static(
            canonical,
            canvas_size=size,
            show_labels=show_labels,
            show_grid=show_grid,
            bg_color=bg_color,
        )
        img.save(out_png, dpi=(150, 150))

    if out_gif:
        gif_size = min(size, 720)
        frames = render_gif_frames(
            canonical,
            canvas_size=gif_size,
            speed=speed,
            show_grid=show_grid,
        )
        frame_ms = int(1000 / max(1, fps))
        frames[0].save(
            out_gif,
            save_all=True,
            append_images=frames[1:],
            duration=frame_ms,
            loop=0,
            optimize=False,
        )

    return {
        "canonical": canonical,
        "out_mp4": mp4_path,
        "out_png": out_png,
        "out_gif": out_gif,
        "dump_json": dump_json,
        "dump_legacy_json": dump_legacy_json,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a minimap from a .wowsreplay or canonical .json file")
    parser.add_argument("replay", help="Input .wowsreplay or canonical .json")
    parser.add_argument("--out", default=None, help="Output MP4 path (default: <input>_minimap.mp4)")
    parser.add_argument("--png", default=None, help="Optional static PNG output path")
    parser.add_argument("--gif", default=None, help="Also save animated GIF")
    parser.add_argument("--size", type=int, default=1024, help="Canvas size px")
    parser.add_argument("--fps", type=int, default=25, help="Output fps")
    parser.add_argument("--speed", type=float, default=3.0, help="Game-seconds per frame (lower = slower playback)")
    parser.add_argument("--no-labels", action="store_true")
    parser.add_argument("--no-grid", action="store_true")
    parser.add_argument("--dump-json", default=None, help="Dump extracted JSON")
    parser.add_argument("--dump-legacy-json", default=None, help="Dump legacy-compatible JSON")
    parser.add_argument("--bg-color", default="10,20,40", help="Background RGB (default: 10,20,40)")
    args = parser.parse_args()

    bg = tuple(int(v) for v in args.bg_color.split(","))
    try:
        result = render_minimap(
            args.replay,
            out_mp4=args.out,
            out_png=args.png,
            out_gif=args.gif,
            dump_json=args.dump_json,
            dump_legacy_json=args.dump_legacy_json,
            size=args.size,
            fps=args.fps,
            speed=args.speed,
            show_labels=not args.no_labels,
            show_grid=not args.no_grid,
            bg_color=bg,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    print(f"Saved MP4: {result['out_mp4']}")
    if result["out_png"]:
        print(f"Saved PNG: {result['out_png']}")
    if result["out_gif"]:
        print(f"Saved GIF: {result['out_gif']}")


if __name__ == "__main__":
    main()
