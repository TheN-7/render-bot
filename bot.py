from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from minimap_render_v2 import render_minimap


LOG = logging.getLogger("render_bot")
CONFIG_PATH = Path(__file__).resolve().with_name("bot_config.json")
DEFAULT_FILE_LIMIT = 8 * 1024 * 1024
MAX_REPLAY_BYTES = 64 * 1024 * 1024


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


class RenderBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        synced = await self.tree.sync()
        LOG.info("Synced %s global command(s)", len(synced))


bot = RenderBot()


@bot.event
async def on_ready() -> None:
    if bot.user is None:
        return
    LOG.info("Logged in as %s (%s)", bot.user.name, bot.user.id)


@bot.tree.command(name="render", description="Render a minimap MP4 from a WoWS replay upload")
@app_commands.describe(
    replay="Upload a .wowsreplay file",
    size="Output canvas size in pixels",
    speed="Game-seconds per frame (lower is slower playback)",
    fps="Output MP4 frames per second",
)
async def render_command(
    interaction: discord.Interaction,
    replay: discord.Attachment,
    size: app_commands.Range[int, 720, 1600] = 1024,
    speed: app_commands.Range[int, 1, 8] = 3,
    fps: app_commands.Range[int, 8, 24] = 12,
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

    try:
        replay_bytes = await replay.read()
    except Exception as exc:
        LOG.exception("Failed to read replay attachment")
        await interaction.followup.send(f"Failed to download the replay: {exc}", ephemeral=True)
        return

    filename = _safe_name(replay.filename)
    stem = Path(filename).stem

    try:
        with tempfile.TemporaryDirectory(prefix="render_bot_") as tmpdir:
            tmp = Path(tmpdir)
            replay_path = tmp / filename
            replay_path.write_bytes(replay_bytes)

            out_mp4 = tmp / f"{stem}_minimap.mp4"

            await asyncio.to_thread(
                render_minimap,
                str(replay_path),
                out_mp4=str(out_mp4),
                size=int(size),
                fps=int(fps),
                speed=int(speed),
                show_labels=True,
                show_grid=True,
            )

            file_limit = int(getattr(interaction.guild, "filesize_limit", DEFAULT_FILE_LIMIT) or DEFAULT_FILE_LIMIT)
            file_size = out_mp4.stat().st_size
            if file_size > file_limit:
                await interaction.followup.send(
                    (
                        f"Render finished, but the MP4 is {file_size / (1024 * 1024):.1f} MB and exceeds this Discord "
                        f"upload limit of {file_limit / (1024 * 1024):.1f} MB. Try a smaller `size` or faster `speed`."
                    ),
                    ephemeral=True,
                )
                return

            with out_mp4.open("rb") as fp:
                discord_file = discord.File(fp, filename=out_mp4.name)
                await interaction.followup.send(
                    content="Render complete. Temporary replay and render files were deleted after upload.",
                    file=discord_file,
                )
    except Exception as exc:
        LOG.exception("Render failed")
        await interaction.followup.send(f"Render failed: {exc}", ephemeral=True)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    token = _load_bot_token()
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
