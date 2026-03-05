#!/usr/bin/env python3
"""
battle_stats_extractor.py
==========================
Extract and aggregate battle statistics from WoWS replays.

Analyzes damage, kills, objectives, and battle performance metrics.
Organized by player, team, and ship class.
"""

import sys
import json
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Any, Tuple
from dataclasses import dataclass, asdict

try:
    from core.replay_schema import to_legacy_schema
except ImportError:
    to_legacy_schema = None


@dataclass
class PlayerStats:
    """Per-player battle statistics."""
    player_name: str
    account_id: str
    ship_id: int
    ship_name: str
    team: str  # "ally" or "enemy"
    
    # Damage stats
    max_hp: int = 0
    damage_taken: int = 0
    damage_dealt: int = 0
    damage_taken_by_fire: int = 0
    
    # Battle results
    is_alive: bool = True
    kill_time: float = None  # When ship was sunk (None if still alive)
    
    # Position data
    first_position: Tuple[float, float, float] = None
    last_position: Tuple[float, float, float] = None
    distance_traveled: float = 0.0
    
    # Battle events
    events: List[Dict] = None
    
    def __post_init__(self):
        if self.events is None:
            self.events = []
    
    def survival_time(self, battle_duration: int) -> float:
        """Calculate how long player survived (as percentage)."""
        if self.is_alive:
            return 100.0
        if self.kill_time is None:
            return 0.0
        return min((self.kill_time / battle_duration) * 100, 100.0)
    
    def damage_ratio(self) -> float:
        """Calculate damage taken vs health."""
        if self.max_hp == 0:
            return 0.0
        return (self.damage_taken / self.max_hp) * 100


@dataclass
class TeamStats:
    """Per-team aggregate statistics."""
    team_id: str
    players: List[PlayerStats] = None
    total_damage_taken: int = 0
    total_kills: int = 0
    victory: bool = False
    
    def __post_init__(self):
        if self.players is None:
            self.players = []
    
    def avg_damage_taken(self) -> float:
        """Average damage taken per player."""
        if not self.players:
            return 0.0
        return self.total_damage_taken / len(self.players)
    
    def avg_survival_time(self, battle_duration: int) -> float:
        """Average survival time for team."""
        if not self.players:
            return 0.0
        return sum(p.survival_time(battle_duration) for p in self.players) / len(self.players)
    
    def player_count(self) -> int:
        return len(self.players)


class BattleStatsExtractor:
    """Extract battle statistics from replay data."""
    
    def __init__(self, replay_data: Dict[str, Any]):
        if "tracks" in replay_data and "events" in replay_data and to_legacy_schema is not None:
            replay_data = to_legacy_schema(replay_data)

        self.replay_data = replay_data
        self.meta = replay_data.get("meta", {})
        self.ships = replay_data.get("ships", {})
        self.vehicles = replay_data.get("vehicles", [])  # Alternative format
        self.teams = replay_data.get("teams", {})
        self.positions = replay_data.get("positions", {})
        self.deaths = replay_data.get("deaths", [])
        self.battle_duration = replay_data.get("battle_end", 0)
        
        # Load ships cache for ship name lookup
        self.ships_cache = self._load_ships_cache()
        
        self.player_stats = {}
        self.team_stats = {}
    
    def _load_ships_cache(self) -> Dict[str, Dict[str, Any]]:
        """Load ships cache for ship name lookup."""
        try:
            import json
            cache_path = Path(__file__).parent / "ships_cache.json"
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
    
    def _get_ship_name(self, ship_id: int) -> str:
        """Get ship name from cache by ship ID."""
        ship_id_str = str(ship_id)
        if ship_id_str in self.ships_cache:
            return self.ships_cache[ship_id_str].get("name", f"Ship_{ship_id}")
        return f"Ship_{ship_id}"
    
    def extract_all(self) -> Dict[str, Any]:
        """Extract all available statistics."""
        
        # Check which format we have (ships dict vs vehicles list)
        if self.vehicles and isinstance(self.vehicles, list):
            self._extract_from_vehicles_list()
        else:
            # Build ship-to-player mapping for dict format
            ship_to_player = self._build_ship_player_mapping()
            self._extract_player_stats(ship_to_player)
        
        # Extract team stats
        self._extract_team_stats()
        
        # Extract battle summary
        battle_summary = self._extract_battle_summary()
        
        return {
            "meta": {
                "map": self.meta.get("mapName", "Unknown"),
                "mode": self.meta.get("gameMode", "Unknown"),
                "duration": self.battle_duration,
                "timestamp": self.meta.get("dateTime", None),
            },
            "player_stats": {k: asdict(v) for k, v in self.player_stats.items()},
            "team_stats": {k: asdict(v) for k, v in self.team_stats.items()},
            "battle_summary": battle_summary,
        }
    
    def _extract_from_vehicles_list(self):
        """Extract stats from vehicles list format."""
        
        for i, vehicle in enumerate(self.vehicles):
            # Determine team - handle both relation and team fields
            relation = vehicle.get("relation")
            team_field = vehicle.get("team")
            
            if relation == "ally":
                team = "ally"
            elif relation == "enemy":
                team = "enemy"
            elif team_field == "player":
                team = "ally"  # The recording player is always on ally team
            else:
                team = "enemy"  # Default fallback
            
            # Extract player stats
            stats = PlayerStats(
                player_name=vehicle.get("name", "Unknown"),
                account_id=str(vehicle.get("meta_eid", "")),
                ship_id=vehicle.get("ship_id", 0),
                ship_name=self._get_ship_name(vehicle.get("ship_id", 0)),
                team=team,
                max_hp=vehicle.get("max_hp", 0),
                damage_taken=vehicle.get("damage_taken", 0),
                damage_dealt=0,  # Damage dealt not available in basic replay format
                damage_taken_by_fire=0,  # Not in this format
                is_alive=not vehicle.get("sunk", False),
                kill_time=vehicle.get("death_clock", None),
                first_position=vehicle.get("first_pos", None),
                last_position=vehicle.get("last_pos", None),
                distance_traveled=0.0,  # Would need position trail to calculate
            )
            
            self.player_stats[i] = stats
    
    def _build_ship_player_mapping(self) -> Dict[str, Dict[str, Any]]:
        """Map ship IDs to player information."""
        mapping = {}
        
        # Parse teams (team0 = allies, team1 = enemies)
        teams_data = self.replay_data.get("teams", {})
        
        team_map = {
            "team0": ("ally", 0),
            "team1": ("enemy", 1),
        }
        
        for team_key, (team_label, relation) in team_map.items():
            if team_key in teams_data:
                for player in teams_data[team_key]:
                    ship_id = player.get("shipId")
                    if ship_id:
                        mapping[ship_id] = {
                            "player_name": player.get("name", "Unknown"),
                            "account_id": player.get("accountDBID", ""),
                            "ship_name": self._get_ship_name(ship_id),
                            "team": team_label,
                            "relation": relation,
                        }
        
        return mapping
    
    def _extract_player_stats(self, ship_mapping: Dict[str, Dict]):
        """Extract statistics for each player."""
        
        # Process each ship
        for ship_id, ship_data in self.ships.items():
            player_info = ship_mapping.get(ship_data.get("shipId"), {})
            
            # Determine if ship was sunk
            is_alive = not ship_data.get("sunk", False)
            kill_time = None
            
            for death in self.deaths:
                if death[0] == ship_id:  # death format: (entity_id, clock)
                    kill_time = death[1]
                    is_alive = False
                    break
            
            # Extract position data
            trail = self.positions.get(ship_id, [])
            first_pos = trail[0][1:4] if trail else None
            last_pos = trail[-1][1:4] if trail else None
            distance = self._calculate_distance(trail)
            
            # Create player stats
            stats = PlayerStats(
                player_name=player_info.get("player_name", "Unknown"),
                account_id=player_info.get("account_id", ""),
                ship_id=ship_data.get("shipId", 0),
                ship_name=player_info.get("ship_name", "Unknown"),
                team=player_info.get("team", "unknown"),
                max_hp=ship_data.get("maxHp", 0),
                damage_taken=ship_data.get("damageTaken", 0),
                damage_dealt=ship_data.get("damageDealt", 0),  # May not be available in dict format
                is_alive=is_alive,
                kill_time=kill_time,
                first_position=first_pos,
                last_position=last_pos,
                distance_traveled=distance,
            )
            
            self.player_stats[ship_id] = stats
    
    def _extract_team_stats(self):
        """Aggregate statistics by team."""
        
        team_players = defaultdict(list)
        
        for ship_id, player_stats in self.player_stats.items():
            team_players[player_stats.team].append(player_stats)
        
        for team_name, players in team_players.items():
            team_stat = TeamStats(team_id=team_name, players=players)
            team_stat.total_damage_taken = sum(p.damage_taken or 0 for p in players)
            team_stat.total_kills = sum(1 for p in players if not p.is_alive)
            
            self.team_stats[team_name] = team_stat
    
    def _extract_battle_summary(self) -> Dict[str, Any]:
        """Extract overall battle summary."""
        
        summary = {
            "total_ships": len(self.player_stats),
            "total_kills": sum(1 for s in self.player_stats.values() if not s.is_alive),
            "survivors": sum(1 for s in self.player_stats.values() if s.is_alive),
            "capture_events": len(self.replay_data.get("capture_pts", [])),
        }
        
        # Determine winner
        ally_survivors = sum(1 for s in self.player_stats.values() 
                            if s.is_alive and s.team == "ally")
        enemy_survivors = sum(1 for s in self.player_stats.values() 
                             if s.is_alive and s.team == "enemy")
        
        if ally_survivors > enemy_survivors:
            summary["winner"] = "allies"
        elif enemy_survivors > ally_survivors:
            summary["winner"] = "enemies"
        else:
            summary["winner"] = "draw"
        
        return summary
    
    @staticmethod
    def _calculate_distance(trail: List[tuple]) -> float:
        """Calculate total distance from position trail."""
        if len(trail) < 2:
            return 0.0
        
        total = 0.0
        for i in range(len(trail) - 1):
            p1 = trail[i]
            p2 = trail[i + 1]
            
            if len(p1) >= 4 and len(p2) >= 4:
                dx = p2[1] - p1[1]
                dy = p2[2] - p1[2]
                dz = p2[3] - p1[3]
                total += (dx**2 + dy**2 + dz**2) ** 0.5
        
        return total


def print_stats_report(stats_data: Dict[str, Any]):
    """Pretty print battle statistics."""
    
    print(f"\n{'='*80}")
    print(f"BATTLE STATISTICS REPORT")
    print(f"{'='*80}")
    
    # Battle info
    meta = stats_data.get("meta", {})
    print(f"\nBattle Info:")
    print(f"  Map: {meta.get('map', 'Unknown')}")
    print(f"  Mode: {meta.get('mode', 'Unknown')}")
    print(f"  Duration: {meta.get('duration', 0)}s")
    
    # Summary
    summary = stats_data.get("battle_summary", {})
    print(f"\nBattle Summary:")
    print(f"  Total Ships: {summary.get('total_ships', 0)}")
    print(f"  Ships Sunk: {summary.get('total_kills', 0)}")
    print(f"  Survivors: {summary.get('survivors', 0)}")
    print(f"  Winner: {summary.get('winner', 'Unknown').upper()}")
    
    # Player stats
    print(f"\nPlayer Statistics:")
    player_stats = stats_data.get("player_stats", {})
    
    if player_stats:
        # Convert values to list if it's a dict
        if isinstance(player_stats, dict):
            players_list = list(player_stats.values())
        else:
            players_list = player_stats
        
        # Sort by team
        allies = [p for p in players_list if p.get("team") == "ally"]
        enemies = [p for p in players_list if p.get("team") == "enemy"]
        
        duration = meta.get("duration", 1)
        
        print(f"\n  ALLIES ({len(allies)} players):")
        for p in sorted(allies, key=lambda x: x.get("damage_taken", 0) or 0, reverse=True):
            status = "ALIVE" if p.get("is_alive") else "SUNK"
            survival = 100.0 if p.get("is_alive") else (p.get("kill_time", 0) / duration * 100 if p.get("kill_time") else 0)
            print(f"    {p.get('player_name', 'Unknown'):20s} | HP: {p.get('damage_taken', 0):5d}/{p.get('max_hp', 0):5d} "
                  f"| {status:6s} ({survival:5.1f}%)")
        
        print(f"\n  ENEMIES ({len(enemies)} players):")
        for p in sorted(enemies, key=lambda x: x.get("damage_taken", 0) or 0, reverse=True):
            status = "ALIVE" if p.get("is_alive") else "SUNK"
            survival = 100.0 if p.get("is_alive") else (p.get("kill_time", 0) / duration * 100 if p.get("kill_time") else 0)
            print(f"    {p.get('player_name', 'Unknown'):20s} | HP: {p.get('damage_taken', 0):5d}/{p.get('max_hp', 0):5d} "
                  f"| {status:6s} ({survival:5.1f}%)")
    
    print(f"\n{'='*80}\n")


def main():
    """Extract battle statistics from replay."""
    
    if len(sys.argv) < 2:
        print("Battle Statistics Extractor")
        print("="*50)
        print("Usage: python battle_stats_extractor.py <replay.json> [output.json]")
        print()
        print("Requires: Replay JSON file from replay_extract.py")
        return
    
    replay_json_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    try:
        with open(replay_json_path, "r", encoding="utf-8") as f:
            replay_data = json.load(f)
        
        print(f"Extracting battle statistics from: {Path(replay_json_path).name}")
        
        # Extract stats
        extractor = BattleStatsExtractor(replay_data)
        stats = extractor.extract_all()
        
        # Print report
        print_stats_report(stats)
        
        # Save to file
        if output_path is None:
            output_path = Path(replay_json_path).stem + "_battle_stats.json"
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)
        
        print(f"[+] Statistics saved to: {output_path}\n")
        
    except FileNotFoundError:
        print(f"ERROR: File not found: {replay_json_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
