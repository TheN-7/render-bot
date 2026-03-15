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
from PIL import Image


ProgressCallback = Callable[[str, int, int], None]
PLAYBACK_DURATION_SCALE = 1.45
MP4_CRF = "17"
MP4_PRESET = "slow"
AUTO_OUTPUT_MIN_S = 55.0
AUTO_OUTPUT_MAX_S = 85.0
AUTO_BATTLE_MAX_S = 1200.0
DUAL_OUTPUT_MAX_WIDTH = 1920
QUALITY_SCALE = 1.5


def _battle_duration_seconds(canonical: Dict[str, Any]) -> float:
    battle_end = float(canonical.get("stats", {}).get("battle_end_s", 0.0))
    battle_start = float(canonical.get("stats", {}).get("battle_start_s", 0.0))
    if battle_end > 0:
        return max(0.0, battle_end - max(0.0, battle_start))
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


def speed_for_output_duration(battle_seconds: float, fps: int, output_duration_s: float) -> float:
    battle_seconds = max(0.0, float(battle_seconds))
    canonical = {"stats": {"battle_end_s": battle_seconds}}
    return _resolve_speed(canonical, fps, 3.0, internal_target_duration_s(output_duration_s))


def _ffmpeg_executable() -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _downscale_frames(frames, scale: float):
    if scale <= 1.01:
        for frame in frames:
            yield frame
        return
    for frame in frames:
        target_w = max(1, int(round(frame.width / scale)))
        target_h = max(1, int(round(frame.height / scale)))
        yield frame.resize((target_w, target_h), Image.Resampling.LANCZOS)


def _save_mp4(
    frames,
    out_mp4: str,
    fps: int,
    progress: ProgressCallback | None = None,
    total_frames: int | None = None,
    *,
    preset: str | None = None,
    crf: str | None = None,
    threads: int | None = None,
) -> None:
    iterator = iter(frames)
    try:
        first_frame = next(iterator)
    except StopIteration as exc:
        raise RuntimeError("No frames were generated for MP4 export") from exc

    def _even_dim(value: int) -> int:
        if value % 2 == 0:
            return value
        return value + 1 if value <= 1 else value - 1

    frame_size = first_frame.size
    target_w = _even_dim(frame_size[0])
    target_h = _even_dim(frame_size[1])
    if (target_w, target_h) != frame_size:
        first_frame = first_frame.resize((target_w, target_h), Image.Resampling.LANCZOS)

        def _resize_to_even(frames_iter):
            for frame in frames_iter:
                if frame.size != (target_w, target_h):
                    yield frame.resize((target_w, target_h), Image.Resampling.LANCZOS)
                else:
                    yield frame

        iterator = _resize_to_even(iterator)
        frame_size = (target_w, target_h)
    fps = max(1, int(fps))
    total = max(1, int(total_frames or 0))
    written = 0

    def _emit(stage: str, current: int, total_count: int) -> None:
        if progress is not None:
            progress(stage, int(current), max(1, int(total_count)))

    preset = str(preset or MP4_PRESET)
    crf = str(crf or MP4_CRF)

    # Preferred path: imageio writer with higher-quality H.264 settings.
    try:
        import numpy as np
        import imageio.v2 as imageio
    except Exception:
        np = None
        imageio = None

    if imageio is not None and np is not None:
        output_params = ["-crf", crf, "-preset", preset, "-movflags", "+faststart"]
        if threads is not None and int(threads) > 0:
            output_params.extend(["-threads", str(int(threads))])
        writer = imageio.get_writer(
            out_mp4,
            fps=fps,
            codec="libx264",
            macro_block_size=None,
            pixelformat="yuv420p",
            output_params=output_params,
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
    ffmpeg = _ffmpeg_executable()
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
            crf,
            "-preset",
            preset,
            *(
                ["-threads", str(int(threads))]
                if threads is not None and int(threads) > 0
                else []
            ),
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


def stack_mp4_side_by_side(
    left_mp4: str,
    right_mp4: str,
    out_mp4: str,
    *,
    fps: int,
    output_duration_s: float,
    progress: ProgressCallback | None = None,
    preset: str | None = None,
    crf: str | None = None,
    threads: int | None = None,
) -> None:
    ffmpeg = _ffmpeg_executable()
    if not ffmpeg:
        raise RuntimeError("Dual MP4 export requires ffmpeg")

    preset = str(preset or MP4_PRESET)
    crf = str(crf or MP4_CRF)
    fps = max(1, int(fps))
    output_duration_s = max(1.0, float(output_duration_s))
    max_width = max(640, int(DUAL_OUTPUT_MAX_WIDTH))
    stop_pad = max(1.0, output_duration_s)
    filter_complex = (
        f"[0:v]fps={fps},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={stop_pad:.3f}[left];"
        f"[1:v]fps={fps},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={stop_pad:.3f}[right];"
        f"[left][right]hstack=inputs=2[stacked];"
        f"[stacked]scale='min({max_width},iw)':-2:flags=lanczos[v]"
    )

    if progress is not None:
        progress("stacking", 0, 1)

    process = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(left_mp4),
            "-i",
            str(right_mp4),
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-an",
            "-t",
            f"{output_duration_s:.3f}",
            "-c:v",
            "libx264",
            "-crf",
            crf,
            "-preset",
            preset,
            *(
                ["-threads", str(int(threads))]
                if threads is not None and int(threads) > 0
                else []
            ),
            "-movflags",
            "+faststart",
            "-pix_fmt",
            "yuv420p",
            str(out_mp4),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode != 0:
        raise RuntimeError(process.stderr.decode("utf-8", errors="replace") or "ffmpeg dual stack export failed")

    if progress is not None:
        progress("stacking", 1, 1)


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
    quality: float = QUALITY_SCALE,
    mp4_preset: str | None = None,
    mp4_crf: str | None = None,
    mp4_threads: int | None = None,
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
    quality = max(1.0, float(quality))
    render_size = max(256, int(round(size * quality)))
    scale = render_size / float(size)
    base = os.path.splitext(str(src))[0]
    mp4_path = out_mp4 or (base + "_minimap.mp4")

    total_frames = estimate_animation_frame_count(canonical, speed=speed)
    if progress is not None:
        progress("rendering", 0, total_frames)
    mp4_frames = iter_animation_frames(
        canonical,
        canvas_size=render_size,
        speed=speed,
        show_grid=show_grid,
    )
    mp4_frames = _downscale_frames(mp4_frames, scale)
    _save_mp4(
        mp4_frames,
        mp4_path,
        fps,
        progress=progress,
        total_frames=total_frames,
        preset=mp4_preset,
        crf=mp4_crf,
        threads=mp4_threads,
    )
    if progress is not None:
        progress("done", total_frames, total_frames)

    if out_png:
        img = render_static(
            canonical,
            canvas_size=render_size,
            show_labels=show_labels,
            show_grid=show_grid,
            bg_color=bg_color,
        )
        if scale > 1.01:
            img = img.resize((int(round(img.width / scale)), int(round(img.height / scale))), Image.Resampling.LANCZOS)
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
    parser.add_argument("--quality", type=float, default=QUALITY_SCALE, help="Supersampling scale (default: 1.5)")
    parser.add_argument("--preset", default=None, help="FFmpeg preset (e.g. veryfast, fast, medium, slow)")
    parser.add_argument("--crf", default=None, help="FFmpeg CRF value (e.g. 17..23)")
    parser.add_argument("--threads", type=int, default=None, help="FFmpeg thread limit (lower = less CPU)")
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
            quality=args.quality,
            mp4_preset=args.preset,
            mp4_crf=args.crf,
            mp4_threads=args.threads,
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
