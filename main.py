#!/usr/bin/env python3
"""
WoWS Replay Analysis - Main Entry Point

Organized structure:
- core/: Core replay parsing and extraction
- api/: API integration and configuration  
- utils/: Utility functions
- tools/: Debug and analysis tools
"""

import sys
import os
from pathlib import Path

# Add subdirectories to path for imports
sys.path.insert(0, str(Path(__file__).parent / "core"))
sys.path.insert(0, str(Path(__file__).parent / "api"))  
sys.path.insert(0, str(Path(__file__).parent / "utils"))

def main():
    """Main entry point for WoWS replay analysis."""
    
    if len(sys.argv) < 2:
        print("WoWS Replay Analysis Tools")
        print("=" * 40)
        print("Usage: python main.py <command> [options]")
        print()
        print("Commands:")
        print("  analyze <replay.wowsreplay>  - Analyze replay with API enhancement")
        print("  extract <replay.wowsreplay>  - Extract data to JSON")
        print("  setup                       - Configure API credentials")
        print("  status                      - Show configuration status")
        print()
        print("Examples:")
        print("  python main.py analyze replay.wowsreplay")
        print("  python main.py setup")
        return
    
    command = sys.argv[1].lower()
    
    if command == "analyze":
        if len(sys.argv) < 3:
            print("Error: Replay file required")
            return
        replay_path = sys.argv[2]
        
        # Import from organized core package
        try:
            from test_enhanced_summary import test_enhanced_summary
            test_enhanced_summary(replay_path)
        except ImportError as e:
            print(f"Error importing analysis module: {e}")
    
    elif command == "extract":
        if len(sys.argv) < 3:
            print("Error: Replay file required")
            return
        replay_path = sys.argv[2]
        
        # Import from organized core package
        try:
            from replay_extract import extract_replay
            extract_replay(replay_path)
        except ImportError as e:
            print(f"Error importing extraction module: {e}")
    
    elif command == "setup":
        # Import from organized api package
        try:
            from setup_api import main as setup_main
            # Override sys.argv for setup
            original_argv = sys.argv
            sys.argv = ["setup_api.py"] + sys.argv[2:]
            try:
                setup_main()
            finally:
                sys.argv = original_argv
        except ImportError as e:
            print(f"Error importing setup module: {e}")
    
    elif command == "status":
        # Import from organized api package
        try:
            from setup_api import show_status
            show_status()
        except ImportError as e:
            print(f"Error importing setup module: {e}")
    
    else:
        print(f"Unknown command: {command}")

if __name__ == "__main__":
    main()
