#!/usr/bin/env python3
"""
Enhanced replay summary with WoWS API integration.

Usage:
    # Set your API credentials first:
    set WWS_APP_ID=your_app_id_here
    set WWS_REALM=na  # or eu, asia, ru
    
    python replay_summary_api.py <replay_file.wowsreplay>
    
    Or create wws_api_config.json:
    {"app_id": "your_app_id_here", "realm": "na"}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import sys
import json
import os
import argparse

from replay_parser import parse_replay
from wows_api import load_credentials, WoWSAPI, create_ship_cache, get_ship_name
from map_names import get_map_name, get_game_mode

@dataclass
class PlayerResult:
    name: str
    account_id: Optional[int] = None
    ship: Optional[str] = None
    ship_id: Optional[int] = None
    team: Optional[int] = None
    damage: Optional[int] = None
    frags: Optional[int] = None
    survived: Optional[bool] = None
    max_hp: Optional[int] = None
    damage_taken: Optional[int] = None
    dmg_pct: Optional[float] = None
    clan_tag: Optional[str] = None

@dataclass
class EnhancedReplaySummary:
    map_name: Optional[str]
    game_mode: Optional[str]
    outcome: Optional[str]
    duration_seconds: Optional[int]
    players: List[PlayerResult]
    player_name: Optional[str] = None
    player_vehicle: Optional[str] = None
    player_clan: Optional[str] = None
    binary_damage_available: bool = False
    decryption_success: bool = False
    api_available: bool = False

def _safe_get(d: Dict[str, Any], *keys: str) -> Optional[Any]:
    for key in keys:
        if key in d:
            return d[key]
    return None

def _extract_players(blocks: List[Any], ships_cache: Dict[int, Dict] = None) -> List[PlayerResult]:
    players: Dict[str, PlayerResult] = {}
    
    for block in blocks:
        if not isinstance(block, dict):
            continue
        
        vehicles = block.get("vehicles") or block.get("players") or block.get("ships")
        if isinstance(vehicles, dict):
            iterable = vehicles.values()
        elif isinstance(vehicles, list):
            iterable = vehicles
        else:
            continue
        
        for value in iterable:
            if not isinstance(value, dict):
                continue
            
            name = _safe_get(value, "name", "playerName", "NickName", "clientUserName")
            if not isinstance(name, str):
                continue
            
            pr = players.get(name) or PlayerResult(name=name)
            
            # Ship information with API enhancement
            ship_id = _safe_get(value, "shipId")
            if ship_id is not None:
                pr.ship_id = int(ship_id)
                if ships_cache:
                    pr.ship = get_ship_name(int(ship_id), ships_cache)
                else:
                    pr.ship = f"Ship {ship_id}"
            
            # Account ID (if available)
            account_id = _safe_get(value, "accountId", "account_id")
            if account_id is not None:
                pr.account_id = int(account_id)
            
            # Team information
            team = value.get("relation") or value.get("teamId") or value.get("team")
            if isinstance(team, int):
                pr.team = team
            
            # Damage statistics
            if pr.damage is None:
                for field in ("totalDamageDealt", "damageDealt", "damage"):
                    v = value.get(field)
                    if isinstance(v, (int, float)):
                        pr.damage = int(v)
                        break
            
            # Frags
            for field in ("frags", "kills"):
                v = value.get(field)
                if isinstance(v, (int, float)):
                    pr.frags = int(v)
                    break
            
            # Survival
            for field in ("survived", "isAlive"):
                v = value.get(field)
                if isinstance(v, bool):
                    pr.survived = v
                    break
            
            players[name] = pr
    
    return list(players.values())

def build_enhanced_summary(blocks: List[Any], 
                          ships_cache: Dict[int, Dict] = None,
                          api_available: bool = False) -> EnhancedReplaySummary:
    map_display_name = game_mode_id = outcome = None
    map_id = None
    duration_seconds = None
    player_name = None
    player_vehicle = None
    
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if not map_display_name:
            map_display_name = _safe_get(block, "mapDisplayName", "mapName")
        if not map_id:
            map_id = _safe_get(block, "mapId")
        if not game_mode_id:
            game_mode_id = _safe_get(block, "gameMode")
        if not outcome:
            outcome = _safe_get(block, "winnerTeam", "winner", "battleResult")
        if duration_seconds is None:
            dur = _safe_get(block, "duration", "battleDuration")
            if isinstance(dur, (int, float)):
                duration_seconds = int(dur)
        if not player_name:
            player_name = _safe_get(block, "playerName")
        if not player_vehicle:
            player_vehicle = _safe_get(block, "playerVehicle")
    
    # Convert to human-readable names
    map_name = get_map_name(map_display_name, map_id)
    game_mode = get_game_mode(int(game_mode_id) if game_mode_id else None)
    
    players = _extract_players(blocks, ships_cache)
    
    return EnhancedReplaySummary(
        map_name=map_name,
        game_mode=game_mode,
        outcome=str(outcome) if outcome is not None else None,
        duration_seconds=duration_seconds,
        players=players,
        player_name=player_name,
        player_vehicle=player_vehicle,
        binary_damage_available=False,  # Would need binary extraction
        decryption_success=False,
        api_available=api_available,
    )

def print_enhanced_summary(summary: EnhancedReplaySummary) -> None:
    print("=" * 120)
    print("ENHANCED BATTLE SUMMARY (with WoWS API)")
    print("=" * 120)
    print(f"Map      : {summary.map_name}")
    print(f"Mode     : {summary.game_mode}")
    print(f"Outcome  : {summary.outcome}")
    print(f"Duration : {summary.duration_seconds}s")
    
    if summary.player_name:
        print(f"Player   : {summary.player_name}")
    if summary.player_vehicle:
        print(f"Vehicle  : {summary.player_vehicle}")
    if summary.player_clan:
        print(f"Clan     : {summary.player_clan}")
    
    api_status = "Available" if summary.api_available else "Not available"
    print(f"API      : {api_status}")
    print()
    
    if not summary.players:
        print("No player/vehicle details found in this replay.")
        return
    
    def sort_key(p: PlayerResult):
        order = {0: 0, 1: 1, 2: 2}
        dmg = p.damage or 0
        return (order.get(p.team, 3), -dmg, p.name.lower())
    
    sorted_players = sorted(summary.players, key=sort_key)
    
    # Enhanced header with ship information
    header = (f"{'Player':<25} {'Ship':<35} {'Clan':<8} "
              f"{'Dmg Dealt':>12} {'DMG%':>6}  {'Frags':>5}  {'Alive':>5}")
    sep = "─" * len(header)
    
    total_damage = sum(p.damage or 0 for p in summary.players)
    
    current_team = None
    for p in sorted_players:
        if p.team != current_team:
            if current_team is not None:
                print()
            current_team = p.team
            labels = {0: "[YOU]", 1: "[ALLIES]", 2: "[ENEMIES]"}
            print(labels.get(p.team, "[UNKNOWN]"))
            print(header)
            print(sep)
        
        # Format enhanced output
        dmg_str = f"{p.damage:,}" if p.damage is not None else "?"
        pct_str = f"{p.damage/total_damage*100:.1f}%" if (p.damage and total_damage) else "N/A"
        frg_str = str(p.frags) if p.frags is not None else ""
        alv_str = ("Y" if p.survived else "N") if p.survived is not None else ""
        clan_str = (p.clan_tag or "")[:7] if p.clan_tag else ""
        ship_str = (p.ship or "")[:34]
        
        print(f"  {p.name[:23]:<23} {ship_str:<35} {clan_str:<8} "
              f"{dmg_str:>12} {pct_str:>6}  {frg_str:>5}  {alv_str:>5}")
    
    print()
    print(sep)
    
    # Team damage breakdown
    ally_damage = sum(p.damage or 0 for p in summary.players if p.team in (0, 1))
    enemy_damage = sum(p.damage or 0 for p in summary.players if p.team == 2)
    
    if total_damage > 0:
        print(f"  {'Ally damage':<45} {ally_damage:>12,}  ({ally_damage/total_damage*100:.1f}%)")
        print(f"  {'Enemy damage':<45} {enemy_damage:>12,}  ({enemy_damage/total_damage*100:.1f}%)")
        print(f"  {'Total damage':<45} {total_damage:>12,}")
    print()

def analyze_replay_with_api(replay_path: str, use_api: bool = True) -> EnhancedReplaySummary:
    """Analyze replay with optional API enhancement."""
    
    ships_cache = {}
    api_available = False
    
    if use_api:
        print("Initializing WoWS API...")
        creds = load_credentials()
        if creds:
            api = WoWSAPI(creds)
            ships_cache = create_ship_cache(api)
            api_available = True
            print("API initialized successfully")
        else:
            print("API credentials not found, using basic extraction")
    
    print(f"Processing replay: {replay_path}")
    blocks = parse_replay(replay_path)
    
    if not blocks:
        raise RuntimeError("Could not parse any JSON blocks from replay file.")
    
    summary = build_enhanced_summary(blocks, ships_cache, api_available)
    print_enhanced_summary(summary)
    
    return summary

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Show enhanced battle summary for a WoWS replay with API integration.")
    parser.add_argument("replay", help="Path to .wowsreplay file")
    parser.add_argument("--no-api", action="store_true",
                        help="Skip API enhancement (basic extraction only)")
    
    args = parser.parse_args()
    
    try:
        analyze_replay_with_api(args.replay, use_api=not args.no_api)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
