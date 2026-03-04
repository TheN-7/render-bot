"""
Core replay parsing and extraction modules.
"""

import sys
import os
from pathlib import Path

# Add parent directories to path for imports
parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))
sys.path.insert(0, str(parent_dir / "api"))
sys.path.insert(0, str(parent_dir / "utils"))

from .replay_parser import parse_replay
from .replay_extract import extract_replay
from .WowsReplayDecoder import load_replay_metadata, read_binary_section, decrypt_binary, parse_packets

# Import the enhanced summary from the working version
import importlib.util
spec = importlib.util.spec_from_file_location(
    "test_enhanced_summary", 
    str(parent_dir / "core" / "test_enhanced_summary.py")
)
test_module = importlib.util.module_from_spec(spec)
sys.modules["test_enhanced_summary"] = test_module

__all__ = [
    'parse_replay',
    'extract_replay', 
    'load_replay_metadata',
    'read_binary_section',
    'decrypt_binary',
    'parse_packets'
]
