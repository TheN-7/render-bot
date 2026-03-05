#!/usr/bin/env python3
"""comprehensive_replay_analysis.py

Runs entity and stats analysis on canonical or legacy replay JSON.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

from core.replay_schema import to_legacy_schema

try:
    from entities_analyzer import analyze_entities_from_replay
    from battle_stats_extractor import BattleStatsExtractor
except ImportError as exc:
    print(f"ERROR: Missing required modules - {exc}")
    sys.exit(1)


class ComprehensiveReplayAnalyzer:
    def __init__(self, replay_path: str):
        self.replay_path = replay_path
        self.replay_name = Path(replay_path).stem
        self.analysis_results: Dict[str, Any] = {}

    def analyze_complete(self) -> Dict[str, Any] | None:
        print("\n" + "=" * 80)
        print("COMPREHENSIVE WOWS REPLAY ANALYSIS")
        print(f"File: {Path(self.replay_path).name}")
        print("=" * 80 + "\n")

        print("[1/4] Loading replay data...")
        try:
            replay_data = self._load_replay_data()
            self.analysis_results["meta"] = replay_data.get("meta", {})
            print("      [+] Loaded replay data")
        except Exception as exc:
            print(f"      [-] Failed to load: {exc}")
            return None

        print("[2/4] Analyzing entities...")
        try:
            entity_analysis = analyze_entities_from_replay(replay_data)
            self.analysis_results["entities"] = entity_analysis
            print(f"      [+] Found {entity_analysis.get('entity_count', 0)} entities")
        except Exception as exc:
            print(f"      [-] Entity analysis failed: {exc}")
            entity_analysis = {}

        print("[3/4] Extracting battle statistics...")
        try:
            extractor = BattleStatsExtractor(replay_data)
            battle_stats = extractor.extract_all()
            self.analysis_results["battle_stats"] = battle_stats
            print("      [+] Battle stats extracted")
        except Exception as exc:
            print(f"      [-] Battle stats extraction failed: {exc}")
            battle_stats = {}

        print("[4/4] Generating insights...")
        try:
            insights = self._generate_insights(replay_data, entity_analysis, battle_stats)
            self.analysis_results["insights"] = insights
            print("      [+] Insights generated")
        except Exception as exc:
            print(f"      [-] Insight generation failed: {exc}")

        print("\n" + "=" * 80)
        print("ANALYSIS COMPLETE")
        print("=" * 80 + "\n")
        return self.analysis_results

    def _load_replay_data(self) -> Dict[str, Any]:
        if self.replay_path.endswith(".json") and os.path.exists(self.replay_path):
            with open(self.replay_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            raise FileNotFoundError("Please provide canonical or legacy replay JSON")

        if "tracks" in data and "events" in data:
            return to_legacy_schema(data)
        return data

    def _generate_insights(self, replay_data: Dict[str, Any], entity_analysis: Dict[str, Any], battle_stats: Dict[str, Any]) -> Dict[str, Any]:
        insights: Dict[str, Any] = {
            "battle_flow": {},
            "key_moments": [],
            "team_performance": {},
            "player_highlights": [],
            "data_summary": {},
        }

        insights["battle_flow"] = {
            "duration": replay_data.get("battle_end", replay_data.get("meta", {}).get("duration", 0)),
            "map": replay_data.get("meta", {}).get("mapDisplayName", "Unknown"),
            "game_mode": replay_data.get("meta", {}).get("gameMode", "Unknown"),
            "total_events": len(replay_data.get("deaths", [])) + len(replay_data.get("capture_pts", [])),
        }

        insights["key_moments"] = [
            {"type": "ship_sunk", "entity_id": d[0], "time": d[1]}
            for d in replay_data.get("deaths", [])
            if isinstance(d, (list, tuple)) and len(d) >= 2
        ]

        summary = battle_stats.get("battle_summary", {})
        if summary:
            insights["team_performance"] = {
                "winner": summary.get("winner", "unknown"),
                "reason": self._determine_win_reason(summary),
                "ally_survivors": len([p for p in battle_stats.get("player_stats", {}).values() if p.get("team") == "ally" and p.get("is_alive")]),
                "enemy_survivors": len([p for p in battle_stats.get("player_stats", {}).values() if p.get("team") == "enemy" and p.get("is_alive")]),
            }

        players = list(battle_stats.get("player_stats", {}).values())
        top_players = sorted(players, key=lambda p: p.get("damage_taken", 0) or 0, reverse=True)[:3]
        insights["player_highlights"] = [
            {
                "name": p.get("player_name", "Unknown"),
                "ship": p.get("ship_name", "Unknown"),
                "damage_taken": p.get("damage_taken", 0),
                "max_hp": p.get("max_hp", 0),
                "alive": p.get("is_alive", False),
            }
            for p in top_players
        ]

        insights["data_summary"] = {
            "sections_available": list(replay_data.keys()),
            "ships_analyzed": len(replay_data.get("ships", {})),
            "positions_tracked": len(replay_data.get("positions", {})),
            "events_recorded": len(replay_data.get("deaths", [])) + len(replay_data.get("capture_pts", [])),
        }
        return insights

    @staticmethod
    def _determine_win_reason(summary: Dict[str, Any]) -> str:
        if summary.get("winner") == "draw":
            return "Draw or insufficient data"
        if summary.get("total_kills", 0) > summary.get("survivors", 0):
            return "Superior elimination"
        return "Survival advantage"

    def save_all_analysis(self, output_dir: str | None = None) -> None:
        out_dir = Path(output_dir) if output_dir else Path(self.replay_path).parent
        out_dir.mkdir(parents=True, exist_ok=True)

        complete = out_dir / f"{self.replay_name}_complete_analysis.json"
        complete.write_text(json.dumps(self.analysis_results, indent=2, default=str), encoding="utf-8")
        print(f"[+] Complete analysis saved to: {complete}")

        mapping = {
            "entities": f"{self.replay_name}_entities.json",
            "battle_stats": f"{self.replay_name}_stats.json",
            "insights": f"{self.replay_name}_insights.json",
        }
        for key, filename in mapping.items():
            if key in self.analysis_results:
                path = out_dir / filename
                path.write_text(json.dumps(self.analysis_results[key], indent=2, default=str), encoding="utf-8")
                print(f"[+] {key} saved to: {path}")

    def print_summary(self) -> None:
        print("\n" + "=" * 80)
        print("ANALYSIS SUMMARY")
        print("=" * 80 + "\n")
        insights = self.analysis_results.get("insights", {})
        flow = insights.get("battle_flow", {})
        print("Battle Flow:")
        for key, value in flow.items():
            print(f"  {key}: {value}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python comprehensive_replay_analysis.py <replay.json> [--output dir]")
        return

    replay_path = sys.argv[1]
    output_dir = None
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            output_dir = sys.argv[idx + 1]

    analyzer = ComprehensiveReplayAnalyzer(replay_path)
    results = analyzer.analyze_complete()
    if results:
        analyzer.print_summary()
        analyzer.save_all_analysis(output_dir)
        print("[+] Analysis complete")


if __name__ == "__main__":
    main()
