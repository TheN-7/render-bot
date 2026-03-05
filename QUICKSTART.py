#!/usr/bin/env python3
"""
QUICK START GUIDE - New Analysis Tools
======================================

This guide shows you how to use the new analysis tools added to your render-bot.
"""

# Example 1: Extract a replay file
# ===========================================================================
# Run this first to convert binary .wowsreplay to usable JSON:
#
# python main.py extract path/to/replay.wowsreplay
#
# This creates: replay.json


# Example 2: Analyze entities
# ===========================================================================
# See entity types, relationships, and movement tracking:
#
# python main.py entities replay.json
#
# Output:
# - Shows entity count, types, and classifications
# - Movement trail analysis (distance traveled, sampling rate)
# - Data quality score
# - Saves: replay_entities_analysis.json


# Example 3: Extract battle statistics
# ===========================================================================
# Get detailed per-player and team statistics:
#
# python main.py battle-stats replay.json
#
# Output shows:
# - Player damage taken vs max HP
# - Survival times (for sunk ships)
# - Team aggregate stats
# - Battle summary (winner, kill counts)
# - Saves: replay_battle_stats.json


# Example 4: Comprehensive analysis (RECOMMENDED)
# ===========================================================================
# Run complete analysis with all tools plus insights:
#
# python main.py comprehensive replay.json
#
# This saves 4 files:
# 1. replay_complete_analysis.json - Everything combined
# 2. replay_entities.json - Entity analysis only
# 3. replay_stats.json - Battle stats only
# 4. replay_insights.json - Strategic insights
#
# With optional output directory:
#
# python main.py comprehensive replay.json --output ./analysis_results/


# Example 5: Using tools directly
# ===========================================================================
# You can also run tools independently:

# Option A: Run entities analyzer directly
import sys
from entities_analyzer import main as entities_main
sys.argv = ["entities_analyzer.py", "replay.json"]
entities_main()

# Option B: Run battle stats extractor directly
from battle_stats_extractor import main as stats_main
sys.argv = ["battle_stats_extractor.py", "replay.json"]
stats_main()

# Option C: Run comprehensive analyzer directly
from comprehensive_replay_analysis import main as comprehensive_main
sys.argv = ["comprehensive_replay_analysis.py", "replay.json", "--output", "./results/"]
comprehensive_main()


# Example 6: Parse results programmatically
# ===========================================================================
import json
from pathlib import Path

# Load analysis results
with open("replay_complete_analysis.json", "r") as f:
    analysis = json.load(f)

# Access different sections
entities = analysis["entities"]
battle_stats = analysis["battle_stats"]
insights = analysis["insights"]

# Example: Find winning team
winner = insights["team_performance"]["winner"]
print(f"Battle won by: {winner.upper()}")

# Example: Find top player
player_stats = battle_stats["player_stats"]
top_player = max(player_stats.values(), key=lambda p: p.get("damage_taken", 0))
print(f"Most engaged player: {top_player['player_name']}")
print(f"Damage taken: {top_player['damage_taken']}/{top_player['max_hp']} HP")


# Example 7: Batch analysis (analyze multiple replays)
# ===========================================================================
from pathlib import Path
from comprehensive_replay_analysis import ComprehensiveReplayAnalyzer

json_dir = Path("./replays")
results = []

for json_file in json_dir.glob("*.json"):
    if "analysis" not in json_file.name:  # Skip analysis files
        print(f"Analyzing {json_file.name}...")
        analyzer = ComprehensiveReplayAnalyzer(str(json_file))
        result = analyzer.analyze_complete()
        results.append(result)
        analyzer.save_all_analysis(json_dir / "analysis_results")

print(f"Analyzed {len(results)} replays")


# Example 8: Custom filtering (find specific events)
# ===========================================================================
import json

with open("replay_complete_analysis.json", "r") as f:
    analysis = json.load(f)

# Find all ships that got sunk
entities = analysis["entities"]
for death in entities["battle_events"]["deaths"]:
    entity_id, kill_time = death
    print(f"Ship {entity_id} sunk at {kill_time}s")

# Find average player damage taken
player_stats = analysis["battle_stats"]["player_stats"]
avg_damage = sum(p["damage_taken"] for p in player_stats.values()) / len(player_stats)
print(f"Average damage taken per player: {avg_damage:.0f}")


# Example 9: Check data quality
# ===========================================================================
with open("replay_entities_analysis.json", "r") as f:
    entity_data = json.load(f)

quality = entity_data["data_quality"]
print(f"Data completeness: {quality['completeness_score']*100:.1f}%")
print(f"Has position tracking: {quality['has_positions']}")
print(f"Has battle events: {quality['has_battle_events']}")


# Example 10: Generate custom reports
# ===========================================================================
import json
from pathlib import Path

def generate_csv_report(analysis_json_path):
    """Generate CSV report from analysis"""
    with open(analysis_json_path, "r") as f:
        data = json.load(f)
    
    player_stats = data["battle_stats"]["player_stats"]
    
    # Write CSV
    csv_path = Path(analysis_json_path).stem + "_report.csv"
    with open(csv_path, "w") as f:
        f.write("Player,Ship,Team,Max HP,Damage Taken,Alive,Survival %\n")
        
        meta = data["battle_stats"]["meta"]
        duration = meta["duration"]
        
        for ship_id, player in player_stats.items():
            survival = 100.0 if player["is_alive"] else (player["kill_time"] / duration * 100)
            f.write(f"{player['player_name']},"
                   f"{player['ship_name']},"
                   f"{player['team']},"
                   f"{player['max_hp']},"
                   f"{player['damage_taken']},"
                   f"{player['is_alive']},"
                   f"{survival:.1f}\n")
    
    print(f"Report saved to: {csv_path}")

generate_csv_report("replay_complete_analysis.json")


# SUMMARY OF NEW COMMANDS
# ===========================================================================
# python main.py extract <replay.wowsreplay>
#   → Converts binary replay to JSON
#
# python main.py entities <replay.json>
#   → Analyzes entity types and relationships
#
# python main.py battle-stats <replay.json>
#   → Extracts battle statistics
#
# python main.py comprehensive <replay.json> [--output dir]
#   → Complete analysis with all insights (RECOMMENDED)
#
# python main.py setup
#   → Configure API credentials (existing)
#
# python main.py status
#   → Show configuration (existing)


# TYPICAL WORKFLOW
# ===========================================================================
# 1. Extract binary replay:
#    python main.py extract path/to/battle.wowsreplay
#
# 2. Run comprehensive analysis:
#    python main.py comprehensive battle.json --output ./results/
#
# 3. Review generated files:
#    - results/battle_complete_analysis.json (everything)
#    - results/battle_insights.json (key findings)
#
# 4. Process results:
#    - Read JSON files into your application
#    - Generate custom reports
#    - Feed into databases
#    - Visualize in dashboards


# NEED HELP?
# ===========================================================================
# Usage: python main.py (with no arguments to see help)
# Docs: See ANALYSIS_TOOLS.md for complete documentation
# Issues: Check that replay.json exists and has required data sections
