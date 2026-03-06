from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

import discord
from discord import app_commands
from discord.ext import commands

from minimap_render_v2 import render_minimap

LOG = logging.getLogger("render_bot")
CONFIG_PATH = Path(__file__).resolve().with_name("bot_config.json")
DEFAULT_FILE_LIMIT = 8 * 1024 * 1024
MAX_REPLAY_BYTES = 64 * 1024 * 1024
DEFAULT_RENDER_SIZE = 1024
DEFAULT_RENDER_FPS = 12
DEFAULT_RENDER_DURATION_S = 35


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


def _result_embed(filename: str, duration_s: int, canonical: dict[str, Any]) -> discord.Embed:
    meta = canonical.get("meta", {}) or {}
    map_name = str(meta.get("map_name_resolved") or meta.get("mapDisplayName") or meta.get("mapId") or "Unknown map")
    player_name = str(meta.get("playerName") or "Unknown player")
    ship_name = _resolve_ship_name(canonical)

    embed = discord.Embed(title="Render Complete", color=discord.Color.green())
    embed.add_field(name="Replay", value=filename, inline=False)
    embed.add_field(name="Map", value=map_name, inline=True)
    embed.add_field(name="Player", value=player_name, inline=True)
    embed.add_field(name="Ship", value=ship_name, inline=True)
    embed.add_field(name="Target Length", value=f"{duration_s}s", inline=True)
    return embed


def _progress_bar(current: int, total: int, width: int = 12) -> str:
    total = max(1, int(total))
    current = max(0, min(int(current), total))
    filled = max(0, min(width, int(round((current / total) * width))))
    return "#" * filled + "-" * (width - filled)

def _render_progress_embed(filename: str, duration_s: int, stage: str, current: int, total: int, started_at: float) -> discord.Embed:
    pct = int(round((max(0, current) / max(1, total)) * 100))
    if stage == "loading":
        title = "Rendering Replay"
        status = "Loading replay data"
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
    embed.add_field(name="Target Length", value=f"{duration_s}s", inline=True)
    embed.add_field(name="Elapsed", value=f"{elapsed}s", inline=True)
    embed.add_field(name="Status", value=status, inline=False)
    embed.add_field(name="Progress", value=f"`{_progress_bar(current, total)}` {pct}%", inline=False)
    return embed


class RenderBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        synced = await self.tree.sync()
        LOG.info("Synced %s global command(s)", len(synced))


bot = RenderBot()




@bot.tree.command(name="render", description="Render a minimap MP4 from a WoWS replay upload")
@app_commands.describe(
    replay="Upload a .wowsreplay file",
    duration_s="Target output video length",
)
async def render_command(
    interaction: discord.Interaction,
    replay: discord.Attachment,
    duration_s: Literal[15, 25, 35] = DEFAULT_RENDER_DURATION_S,
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

    async def _set_progress(stage: str, current: int, total: int) -> None:
        async with progress_lock:
            progress_state["stage"] = stage
            progress_state["current"] = max(0, int(current))
            progress_state["total"] = max(1, int(total))

    loop = asyncio.get_running_loop()

    def _progress_callback(stage: str, current: int, total: int) -> None:
        asyncio.run_coroutine_threadsafe(_set_progress(stage, current, total), loop)

    await interaction.edit_original_response(
        embed=_render_progress_embed(filename, int(duration_s), "loading", 0, 1, started_at),
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
                        embed=_render_progress_embed(filename, int(duration_s), stage, current, total, started_at),
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

            out_mp4 = tmp / f"{stem}_minimap.mp4"

            result = await asyncio.to_thread(
                render_minimap,
                str(replay_path),
                out_mp4=str(out_mp4),
                size=DEFAULT_RENDER_SIZE,
                fps=DEFAULT_RENDER_FPS,
                speed=3.0,
                target_duration_s=float(duration_s),
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
                    embed=_result_embed(filename, int(duration_s), result.get("canonical", {}) or {}),
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
