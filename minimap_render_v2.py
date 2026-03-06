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

from core.minimap_data import load_canonical_data, canonical_to_legacy
from renderers.minimap_renderer import iter_animation_frames, render_static, render_gif_frames


def _save_mp4(frames, out_mp4: str, fps: int) -> None:
    iterator = iter(frames)
    try:
        first_frame = next(iterator)
    except StopIteration as exc:
        raise RuntimeError("No frames were generated for MP4 export") from exc

    frame_size = first_frame.size
    fps = max(1, int(fps))

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
            output_params=["-crf", "18", "-preset", "medium", "-movflags", "+faststart"],
        )
        try:
            writer.append_data(np.array(first_frame.convert("RGB")))
            for frame in iterator:
                writer.append_data(np.array(frame.convert("RGB")))
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
            "18",
            "-preset",
            "medium",
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
        assert process.stdin is not None
        process.stdin.write(first_frame.convert("RGB").tobytes())
        for frame in iterator:
            process.stdin.write(frame.convert("RGB").tobytes())
        process.stdin.close()
        _, stderr = process.communicate()
    finally:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace") or "ffmpeg MP4 export failed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a minimap from a .wowsreplay or canonical .json file")
    parser.add_argument("replay", help="Input .wowsreplay or canonical .json")
    parser.add_argument("--out", default=None, help="Output MP4 path (default: <input>_minimap.mp4)")
    parser.add_argument("--png", default=None, help="Optional static PNG output path")
    parser.add_argument("--gif", default=None, help="Also save animated GIF")
    parser.add_argument("--size", type=int, default=1024, help="Canvas size px")
    parser.add_argument("--fps", type=int, default=12, help="GIF fps")
    parser.add_argument("--speed", type=int, default=3, help="Game-seconds per frame (lower = slower playback)")
    parser.add_argument("--no-labels", action="store_true")
    parser.add_argument("--no-grid", action="store_true")
    parser.add_argument("--dump-json", default=None, help="Dump extracted JSON")
    parser.add_argument("--dump-legacy-json", default=None, help="Dump legacy-compatible JSON")
    parser.add_argument("--bg-color", default="10,20,40", help="Background RGB (default: 10,20,40)")
    args = parser.parse_args()

    if not os.path.isfile(args.replay):
        print(f"ERROR: file not found: {args.replay}")
        sys.exit(1)

    canonical = load_canonical_data(args.replay)

    if args.dump_json:
        with open(args.dump_json, "w", encoding="utf-8") as f:
            json.dump(canonical, f, indent=2)

    if args.dump_legacy_json:
        legacy = canonical_to_legacy(canonical)
        with open(args.dump_legacy_json, "w", encoding="utf-8") as f:
            json.dump(legacy, f, indent=2)

    base = os.path.splitext(args.replay)[0]
    out_mp4 = args.out or (base + "_minimap.mp4")
    out_png = args.png
    bg = tuple(int(v) for v in args.bg_color.split(","))

    mp4_frames = iter_animation_frames(
        canonical,
        canvas_size=args.size,
        speed=args.speed,
        show_grid=not args.no_grid,
    )
    _save_mp4(mp4_frames, out_mp4, args.fps)
    print(f"Saved MP4: {out_mp4}")

    if out_png:
        img = render_static(
            canonical,
            canvas_size=args.size,
            show_labels=not args.no_labels,
            show_grid=not args.no_grid,
            bg_color=bg,
        )
        img.save(out_png, dpi=(150, 150))
        print(f"Saved PNG: {out_png}")

    if args.gif:
        gif_size = min(args.size, 720)
        frames = render_gif_frames(
            canonical,
            canvas_size=gif_size,
            speed=args.speed,
            show_grid=not args.no_grid,
        )
        frame_ms = int(1000 / max(1, args.fps))
        frames[0].save(
            args.gif,
            save_all=True,
            append_images=frames[1:],
            duration=frame_ms,
            loop=0,
            optimize=False,
        )
        print(f"Saved GIF: {args.gif}")


if __name__ == "__main__":
    main()
