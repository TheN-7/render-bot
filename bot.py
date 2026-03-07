from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Any

import discord
from discord import app_commands

from core.minimap_data import load_canonical_data
from minimap_render_v2 import auto_output_duration_s, render_minimap, speed_for_output_duration, stack_mp4_side_by_side

LOG = logging.getLogger("render_bot")
CONFIG_PATH = Path(__file__).resolve().with_name("bot_config.json")
COOLDOWN_PATH = Path(__file__).resolve().with_name("bot_cooldowns.json")
DEFAULT_FILE_LIMIT = 8 * 1024 * 1024
MAX_REPLAY_BYTES = 64 * 1024 * 1024
DEFAULT_RENDER_SIZE = 1024
DEFAULT_RENDER_FPS = 25
DUAL_RENDER_SIZE = 720
RENDER_COOLDOWN_S = 120
COOLDOWN_LOCK = asyncio.Lock()


def _load_bot_token() -> str:
    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing bot config file: {CONFIG_PATH.name}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {CONFIG_PATH.name}: {exc}") from exc

    if not isinstance(data, dict):
        raise SystemExit(f"{CONFIG_PATH.name} must contain a JSON object")

    token = str(data.get("token", "") or "").strip()
    if not token:
        raise SystemExit(f"`token` is missing in {CONFIG_PATH.name}")
    return token


def _safe_name(filename: str) -> str:
    name = Path(str(filename or "battle.wowsreplay")).name
    return name or "battle.wowsreplay"


def _load_cooldowns(now_s: float | None = None) -> dict[str, float]:
    now = float(time.time() if now_s is None else now_s)
    try:
        raw = COOLDOWN_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except Exception:
        LOG.exception("Failed to read cooldown state")
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        LOG.warning("Invalid JSON in %s; resetting cooldown state", COOLDOWN_PATH.name)
        return {}

    if not isinstance(data, dict):
        return {}

    cooldowns: dict[str, float] = {}
    for key, value in data.items():
        try:
            user_id = str(int(key))
            expires_at = float(value)
        except (TypeError, ValueError):
            continue
        if expires_at > now:
            cooldowns[user_id] = expires_at
    return cooldowns


def _save_cooldowns(cooldowns: dict[str, float]) -> None:
    tmp_path = COOLDOWN_PATH.with_suffix(".tmp")
    payload = json.dumps(cooldowns, indent=2, sort_keys=True)
    tmp_path.write_text(payload, encoding="utf-8")
    tmp_path.replace(COOLDOWN_PATH)


def _format_wait_time(seconds: int) -> str:
    remaining = max(1, int(seconds))
    minutes, secs = divmod(remaining, 60)
    if minutes and secs:
        return f"{minutes}m {secs:02d}s"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


async def _claim_render_cooldown(user_id: int) -> int:
    now = time.time()
    key = str(int(user_id))
    async with COOLDOWN_LOCK:
        cooldowns = _load_cooldowns(now)
        expires_at = float(cooldowns.get(key, 0.0) or 0.0)
        remaining = max(0.0, expires_at - now)
        if remaining > 0.0:
            return int(remaining + 0.999)
        cooldowns[key] = now + float(RENDER_COOLDOWN_S)
        _save_cooldowns(cooldowns)
        return 0


async def _enforce_render_cooldown(interaction: discord.Interaction) -> bool:
    remaining_s = await _claim_render_cooldown(interaction.user.id)
    if remaining_s <= 0:
        return True
    await interaction.response.send_message(
        f"Render cooldown active. Try again in {_format_wait_time(remaining_s)}.",
        ephemeral=True,
    )
    return False


def _is_replay_attachment(attachment: discord.Attachment) -> bool:
    return Path(attachment.filename or "").suffix.lower() == ".wowsreplay"


def _load_ship_cache() -> dict[str, dict[str, Any]]:
    cache_path = Path(__file__).resolve().with_name("ships_cache.json")
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _resolve_ship_name(canonical: dict[str, Any]) -> str:
    meta = canonical.get("meta", {}) or {}
    vehicles = meta.get("vehicles", []) or []
    player_name = str(meta.get("playerName") or "").strip()
    ship_id = None
    if isinstance(vehicles, list):
        for vehicle in vehicles:
            if not isinstance(vehicle, dict):
                continue
            if str(vehicle.get("name") or "").strip() == player_name:
                ship_id = vehicle.get("shipId")
                break
    try:
        key = str(int(ship_id))
    except (TypeError, ValueError):
        key = ""
    if key:
        ship_name = str(_load_ship_cache().get(key, {}).get("name") or "").strip()
        if ship_name:
            return ship_name
    return str(meta.get("playerVehicle") or "").strip() or "Unknown ship"


def _result_embed(filename: str, output_length_label: str, canonical: dict[str, Any]) -> discord.Embed:
    meta = canonical.get("meta", {}) or {}
    map_name = str(meta.get("map_name_resolved") or meta.get("mapDisplayName") or meta.get("mapId") or "Unknown map")
    player_name = str(meta.get("playerName") or "Unknown player")
    ship_name = _resolve_ship_name(canonical)

    embed = discord.Embed(title="Render Complete", color=discord.Color.green())
    embed.add_field(name="Replay", value=filename, inline=False)
    embed.add_field(name="Map", value=map_name, inline=True)
    embed.add_field(name="Player", value=player_name, inline=True)
    embed.add_field(name="Ship", value=ship_name, inline=True)
    embed.add_field(name="Output Length", value=output_length_label, inline=True)
    return embed


def _dual_result_embed(
    filename_a: str,
    filename_b: str,
    output_length_label: str,
    canonical_a: dict[str, Any],
    canonical_b: dict[str, Any],
) -> discord.Embed:
    meta_a = canonical_a.get("meta", {}) or {}
    meta_b = canonical_b.get("meta", {}) or {}
    map_name = str(
        meta_a.get("map_name_resolved")
        or meta_a.get("mapDisplayName")
        or meta_b.get("map_name_resolved")
        or meta_b.get("mapDisplayName")
        or "Unknown map"
    )
    player_a = str(meta_a.get("playerName") or "Unknown player")
    player_b = str(meta_b.get("playerName") or "Unknown player")
    ship_a = _resolve_ship_name(canonical_a)
    ship_b = _resolve_ship_name(canonical_b)

    embed = discord.Embed(title="Dual Render Complete", color=discord.Color.green())
    embed.add_field(name="Map", value=map_name, inline=False)
    embed.add_field(name="View A", value=f"{player_a}\n{ship_a}", inline=True)
    embed.add_field(name="View B", value=f"{player_b}\n{ship_b}", inline=True)
    embed.add_field(name="Output Length", value=output_length_label, inline=True)
    embed.add_field(name="Replay A", value=filename_a, inline=False)
    embed.add_field(name="Replay B", value=filename_b, inline=False)
    return embed


def _progress_bar(current: int, total: int, width: int = 12) -> str:
    total = max(1, int(total))
    current = max(0, min(int(current), total))
    filled = max(0, min(width, int(round((current / total) * width))))
    return "#" * filled + "-" * (width - filled)

def _render_progress_embed(filename: str, output_length_label: str, stage: str, current: int, total: int, started_at: float) -> discord.Embed:
    pct = int(round((max(0, current) / max(1, total)) * 100))
    if stage == "loading":
        title = "Rendering Replay"
        status = "Loading replay data"
    elif stage == "rendering_a":
        title = "Rendering Dual Replay"
        status = "Rendering view A"
    elif stage == "encoding_a":
        title = "Rendering Dual Replay"
        status = "Encoding view A"
    elif stage == "rendering_b":
        title = "Rendering Dual Replay"
        status = "Rendering view B"
    elif stage == "encoding_b":
        title = "Rendering Dual Replay"
        status = "Encoding view B"
    elif stage == "stacking":
        title = "Rendering Dual Replay"
        status = "Combining both views"
    elif stage == "encoding":
        title = "Rendering Replay"
        status = "Encoding MP4 frames"
    elif stage == "done":
        title = "Render Complete"
        status = "Upload ready"
    else:
        title = "Rendering Replay"
        status = "Preparing render"

    elapsed = max(0, int(time.monotonic() - started_at))
    embed = discord.Embed(title=title, color=discord.Color.blurple())
    embed.add_field(name="Replay", value=filename, inline=False)
    embed.add_field(name="Output Length", value=output_length_label, inline=True)
    embed.add_field(name="Elapsed", value=f"{elapsed}s", inline=True)
    embed.add_field(name="Status", value=status, inline=False)
    embed.add_field(name="Progress", value=f"`{_progress_bar(current, total)}` {pct}%", inline=False)
    return embed


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _battle_identity_error(canonical_a: dict[str, Any], canonical_b: dict[str, Any]) -> str | None:
    meta_a = canonical_a.get("meta", {}) or {}
    meta_b = canonical_b.get("meta", {}) or {}

    arena_a = _safe_int(meta_a.get("battle_arena_id"))
    arena_b = _safe_int(meta_b.get("battle_arena_id"))
    if arena_a is not None and arena_b is not None and arena_a != arena_b:
        return "The replays are from different battles."

    map_a = str(meta_a.get("mapDisplayName") or meta_a.get("mapName") or meta_a.get("mapId") or "").strip()
    map_b = str(meta_b.get("mapDisplayName") or meta_b.get("mapName") or meta_b.get("mapId") or "").strip()
    if map_a and map_b and map_a != map_b:
        return "The replays are on different maps."

    dt_a = str(meta_a.get("dateTime") or "").strip()
    dt_b = str(meta_b.get("dateTime") or "").strip()
    if dt_a and dt_b and dt_a != dt_b:
        return "The replays have different battle start times."

    team_a = _safe_int(meta_a.get("local_team_id"))
    team_b = _safe_int(meta_b.get("local_team_id"))
    if team_a is not None and team_b is not None and team_a == team_b:
        return "Both replays appear to be from the same team."

    return None


def _dual_output_filename(stem_a: str, stem_b: str) -> str:
    safe_a = stem_a[:48].strip() or "view_a"
    safe_b = stem_b[:48].strip() or "view_b"
    return f"{safe_a}__{safe_b}_dual_minimap.mp4"


class RenderBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        synced = await self.tree.sync()
        LOG.info("Synced %s global command(s)", len(synced))


bot = RenderBot()




@bot.tree.command(name="render", description="Render a minimap MP4 from a WoWS replay upload")
@app_commands.describe(
    replay="Upload a .wowsreplay file",
)
async def render_command(
    interaction: discord.Interaction,
    replay: discord.Attachment,
) -> None:
    if not _is_replay_attachment(replay):
        await interaction.response.send_message("Upload a `.wowsreplay` file.", ephemeral=True)
        return

    if replay.size and replay.size > MAX_REPLAY_BYTES:
        await interaction.response.send_message(
            f"Replay is too large ({replay.size / (1024 * 1024):.1f} MB). Limit is {MAX_REPLAY_BYTES / (1024 * 1024):.0f} MB.",
            ephemeral=True,
        )
        return

    if not await _enforce_render_cooldown(interaction):
        return

    await interaction.response.defer(thinking=True)
    started_at = time.monotonic()

    try:
        replay_bytes = await replay.read()
    except Exception as exc:
        LOG.exception("Failed to read replay attachment")
        await interaction.followup.send(f"Failed to download the replay: {exc}", ephemeral=True)
        return

    filename = _safe_name(replay.filename)
    stem = Path(filename).stem
    progress_state = {"stage": "loading", "current": 0, "total": 1}
    progress_lock = asyncio.Lock()
    output_length_label = "auto"

    async def _set_progress(stage: str, current: int, total: int) -> None:
        async with progress_lock:
            progress_state["stage"] = stage
            progress_state["current"] = max(0, int(current))
            progress_state["total"] = max(1, int(total))

    loop = asyncio.get_running_loop()

    def _progress_callback(stage: str, current: int, total: int) -> None:
        asyncio.run_coroutine_threadsafe(_set_progress(stage, current, total), loop)

    await interaction.edit_original_response(
        embed=_render_progress_embed(filename, output_length_label, "loading", 0, 1, started_at),
        attachments=[],
        content=None,
    )

    async def _progress_updater() -> None:
        last_sent: tuple[str, int, int] | None = None
        while True:
            async with progress_lock:
                stage = str(progress_state["stage"])
                current = int(progress_state["current"])
                total = int(progress_state["total"])
            snapshot = (stage, current, total)
            if snapshot != last_sent:
                try:
                    await interaction.edit_original_response(
                        embed=_render_progress_embed(filename, output_length_label, stage, current, total, started_at),
                        attachments=[],
                        content=None,
                    )
                except Exception:
                    LOG.exception("Failed to update render progress message")
                    return
                last_sent = snapshot
            if stage == "done":
                return
            await asyncio.sleep(1.0)

    progress_task = asyncio.create_task(_progress_updater())

    try:
        with tempfile.TemporaryDirectory(prefix="render_bot_") as tmpdir:
            tmp = Path(tmpdir)
            replay_path = tmp / filename
            replay_path.write_bytes(replay_bytes)
            canonical = await asyncio.to_thread(load_canonical_data, str(replay_path))
            output_length_s = auto_output_duration_s(canonical)
            output_length_label = f"{int(round(output_length_s))}s"
            battle_seconds = float((canonical.get("stats", {}) or {}).get("battle_end_s") or 0.0)
            render_speed = speed_for_output_duration(battle_seconds, DEFAULT_RENDER_FPS, output_length_s)

            out_mp4 = tmp / f"{stem}_minimap.mp4"

            result = await asyncio.to_thread(
                render_minimap,
                str(replay_path),
                canonical=canonical,
                out_mp4=str(out_mp4),
                size=DEFAULT_RENDER_SIZE,
                fps=DEFAULT_RENDER_FPS,
                speed=render_speed,
                target_duration_s=None,
                show_labels=True,
                show_grid=True,
                progress=_progress_callback,
            )
            await _set_progress("done", 1, 1)
            await progress_task

            file_limit = int(getattr(interaction.guild, "filesize_limit", DEFAULT_FILE_LIMIT) or DEFAULT_FILE_LIMIT)
            file_size = out_mp4.stat().st_size
            if file_size > file_limit:
                await interaction.edit_original_response(
                    embed=None,
                    content=(
                        f"Render finished, but the MP4 is {file_size / (1024 * 1024):.1f} MB and exceeds this Discord "
                        f"upload limit of {file_limit / (1024 * 1024):.1f} MB."
                    ),
                    attachments=[],
                )
                return

            with out_mp4.open("rb") as fp:
                discord_file = discord.File(fp, filename=out_mp4.name)
                await interaction.delete_original_response()
                await interaction.followup.send(
                    embed=_result_embed(filename, output_length_label, result.get("canonical", {}) or {}),
                    file=discord_file,
                )
    except Exception as exc:
        LOG.exception("Render failed")
        if not progress_task.done():
            progress_task.cancel()
        await interaction.edit_original_response(
            embed=None,
            content=f"Render failed: {exc}",
            attachments=[],
        )


@bot.tree.command(name="render_dual", description="Render a synchronized side-by-side MP4 from two WoWS replays of the same battle")
@app_commands.describe(
    replay_a="First .wowsreplay file",
    replay_b="Second .wowsreplay file from the other team",
)
async def render_dual_command(
    interaction: discord.Interaction,
    replay_a: discord.Attachment,
    replay_b: discord.Attachment,
) -> None:
    if not _is_replay_attachment(replay_a) or not _is_replay_attachment(replay_b):
        await interaction.response.send_message("Upload two `.wowsreplay` files.", ephemeral=True)
        return

    for attachment in (replay_a, replay_b):
        if attachment.size and attachment.size > MAX_REPLAY_BYTES:
            await interaction.response.send_message(
                f"`{attachment.filename}` is too large ({attachment.size / (1024 * 1024):.1f} MB). "
                f"Limit is {MAX_REPLAY_BYTES / (1024 * 1024):.0f} MB.",
                ephemeral=True,
            )
            return

    if not await _enforce_render_cooldown(interaction):
        return

    await interaction.response.defer(thinking=True)
    started_at = time.monotonic()

    try:
        replay_a_bytes = await replay_a.read()
        replay_b_bytes = await replay_b.read()
    except Exception as exc:
        LOG.exception("Failed to read dual replay attachments")
        await interaction.followup.send(f"Failed to download the replays: {exc}", ephemeral=True)
        return

    filename_a = _safe_name(replay_a.filename)
    filename_b = _safe_name(replay_b.filename)
    stem_a = Path(filename_a).stem
    stem_b = Path(filename_b).stem
    label = f"{filename_a}\n{filename_b}"
    progress_state = {"stage": "loading", "current": 0, "total": 1}
    progress_lock = asyncio.Lock()
    output_length_label = "auto"

    async def _set_progress(stage: str, current: int, total: int) -> None:
        async with progress_lock:
            progress_state["stage"] = stage
            progress_state["current"] = max(0, int(current))
            progress_state["total"] = max(1, int(total))

    loop = asyncio.get_running_loop()

    def _progress_callback(stage: str, current: int, total: int) -> None:
        asyncio.run_coroutine_threadsafe(_set_progress(stage, current, total), loop)

    await interaction.edit_original_response(
        embed=_render_progress_embed(label, output_length_label, "loading", 0, 1, started_at),
        attachments=[],
        content=None,
    )

    async def _progress_updater() -> None:
        last_sent: tuple[str, int, int] | None = None
        while True:
            async with progress_lock:
                stage = str(progress_state["stage"])
                current = int(progress_state["current"])
                total = int(progress_state["total"])
            snapshot = (stage, current, total)
            if snapshot != last_sent:
                try:
                    await interaction.edit_original_response(
                        embed=_render_progress_embed(label, output_length_label, stage, current, total, started_at),
                        attachments=[],
                        content=None,
                    )
                except Exception:
                    LOG.exception("Failed to update dual render progress message")
                    return
                last_sent = snapshot
            if stage == "done":
                return
            await asyncio.sleep(1.0)

    progress_task = asyncio.create_task(_progress_updater())

    try:
        with tempfile.TemporaryDirectory(prefix="render_dual_bot_") as tmpdir:
            tmp = Path(tmpdir)
            replay_a_path = tmp / filename_a
            replay_b_path = tmp / filename_b
            replay_a_path.write_bytes(replay_a_bytes)
            replay_b_path.write_bytes(replay_b_bytes)

            canonical_a = await asyncio.to_thread(load_canonical_data, str(replay_a_path))
            canonical_b = await asyncio.to_thread(load_canonical_data, str(replay_b_path))

            identity_error = _battle_identity_error(canonical_a, canonical_b)
            if identity_error:
                if not progress_task.done():
                    progress_task.cancel()
                await interaction.edit_original_response(embed=None, content=identity_error, attachments=[])
                return

            output_length_s = max(auto_output_duration_s(canonical_a), auto_output_duration_s(canonical_b))
            output_length_label = f"{int(round(output_length_s))}s"
            battle_seconds = max(
                float((canonical_a.get("stats", {}) or {}).get("battle_end_s") or 0.0),
                float((canonical_b.get("stats", {}) or {}).get("battle_end_s") or 0.0),
            )
            render_speed = speed_for_output_duration(battle_seconds, DEFAULT_RENDER_FPS, output_length_s)

            left_mp4 = tmp / f"{stem_a}_left.mp4"
            right_mp4 = tmp / f"{stem_b}_right.mp4"
            out_mp4 = tmp / _dual_output_filename(stem_a, stem_b)

            def _progress_a(stage: str, current: int, total: int) -> None:
                mapped = "encoding_a" if stage == "encoding" else "rendering_a"
                _progress_callback(mapped, current, total)

            def _progress_b(stage: str, current: int, total: int) -> None:
                mapped = "encoding_b" if stage == "encoding" else "rendering_b"
                _progress_callback(mapped, current, total)

            await asyncio.to_thread(
                render_minimap,
                str(replay_a_path),
                canonical=canonical_a,
                out_mp4=str(left_mp4),
                size=DUAL_RENDER_SIZE,
                fps=DEFAULT_RENDER_FPS,
                speed=render_speed,
                target_duration_s=None,
                show_labels=True,
                show_grid=True,
                progress=_progress_a,
            )
            await asyncio.to_thread(
                render_minimap,
                str(replay_b_path),
                canonical=canonical_b,
                out_mp4=str(right_mp4),
                size=DUAL_RENDER_SIZE,
                fps=DEFAULT_RENDER_FPS,
                speed=render_speed,
                target_duration_s=None,
                show_labels=True,
                show_grid=True,
                progress=_progress_b,
            )
            await asyncio.to_thread(
                stack_mp4_side_by_side,
                str(left_mp4),
                str(right_mp4),
                str(out_mp4),
                fps=DEFAULT_RENDER_FPS,
                output_duration_s=output_length_s,
                progress=_progress_callback,
            )
            await _set_progress("done", 1, 1)
            await progress_task

            file_limit = int(getattr(interaction.guild, "filesize_limit", DEFAULT_FILE_LIMIT) or DEFAULT_FILE_LIMIT)
            file_size = out_mp4.stat().st_size
            if file_size > file_limit:
                await interaction.edit_original_response(
                    embed=None,
                    content=(
                        f"Dual render finished, but the MP4 is {file_size / (1024 * 1024):.1f} MB and exceeds this Discord "
                        f"upload limit of {file_limit / (1024 * 1024):.1f} MB."
                    ),
                    attachments=[],
                )
                return

            with out_mp4.open("rb") as fp:
                discord_file = discord.File(fp, filename=out_mp4.name)
                await interaction.delete_original_response()
                await interaction.followup.send(
                    embed=_dual_result_embed(filename_a, filename_b, output_length_label, canonical_a, canonical_b),
                    file=discord_file,
                )
    except Exception as exc:
        LOG.exception("Dual render failed")
        if not progress_task.done():
            progress_task.cancel()
        await interaction.edit_original_response(
            embed=None,
            content=f"Dual render failed: {exc}",
            attachments=[],
        )



@bot.event
async def on_ready() -> None:
    if bot.user is None:
        return
    LOG.info("Logged in as %s (%s)", bot.user.name, bot.user.id)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    token = _load_bot_token()
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
