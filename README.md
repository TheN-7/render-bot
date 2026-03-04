# WoWS Replay Analysis - Organized Structure

## 📁 **Directory Organization**

```
render/
├── main.py                 # Main entry point
├── api/                    # API integration
│   ├── __init__.py
│   ├── wows_api.py         # Core API module
│   └── setup_api.py        # Configuration utility
├── core/                   # Core replay processing
│   ├── __init__.py
│   ├── replay_parser.py     # JSON parsing
│   ├── WowsReplayDecoder.py # Binary decoding
│   ├── replay_extract.py    # Data extraction
│   ├── replay_summary_api.py # Enhanced analysis
│   └── test_enhanced_summary.py # Unicode-safe version
├── utils/                  # Utilities
│   ├── __init__.py
│   └── map_names.py       # Map/game mode conversion
├── tools/                  # Debug and analysis tools
│   ├── debug_api.py         # API debugging
│   ├── debug_api2.py        # Extended debugging
│   └── check_ship_names.py  # Ship name verification
├── ships_cache.json         # Ship database (953 ships)
├── wows_api_config.json     # API credentials
└── data/                   # Generated replay data
```

## 🚀 **Usage**

### **Main Entry Point**
```bash
# Analyze replay with full API enhancement
python main.py analyze your_replay.wowsreplay

# Extract data to JSON
python main.py extract your_replay.wowsreplay

# Configure API credentials
python main.py setup

# Check configuration status
python main.py status
```

### **Direct Module Usage**
```bash
# Core analysis
python core/replay_summary_api.py replay.wowsreplay

# API configuration
python api/setup_api.py cache

# Utilities
python utils/map_names.py
```

## 📊 **Features**

### **✅ Enhanced Capabilities**
- **953 Ships**: Complete database with tiers, classes, names
- **API Integration**: Full WoWS API connectivity
- **Smart Resolution**: Ship ID → Human-readable names
- **Unicode Safe**: Handles special characters properly
- **Organized Structure**: Clear separation of concerns

### **🎯 Battle Analysis**
- **Map Names**: "Two Brothers" instead of internal IDs
- **Game Modes**: "Random" instead of numeric codes
- **Ship Details**: Complete information for all players
- **Team Intelligence**: [YOU], [ALLY], [ENEMY] labels
- **Professional Output**: Clean, formatted summaries

### **🔧 Configuration**
- **Setup Wizard**: Interactive API credential configuration
- **Status Checking**: Verify API connectivity
- **Ship Caching**: Local database for fast lookups
- **Rate Limiting**: Built-in API request management

## 📈 **Before vs After**

### **Before (Chaotic)**
- 30+ Python files in root directory
- Multiple versions of same functionality
- Debug tools mixed with core code
- No clear entry points
- Redundant imports and dependencies

### **After (Organized)**
- 4 logical directories by function
- Single main entry point
- Clear module boundaries
- Proper package structure
- Essential files only: 16 total

## 🛠️ **Development Benefits**

### **Easier Maintenance**
- **Clear Scope**: Each directory has specific purpose
- **Modular Design**: Components can be updated independently
- **Package Structure**: Proper Python imports with __init__.py
- **Testable**: Individual modules can be tested separately

### **Better User Experience**
- **Single Command**: `python main.py analyze replay.wowsreplay`
- **Clear Help**: Built-in usage instructions
- **Error Handling**: Graceful fallbacks and informative errors
- **Professional Output**: Enhanced battle summaries

### **Scalable Architecture**
- **API Layer**: Easy to extend with new endpoints
- **Core Processing**: Replay parsing separated from presentation
- **Utility Functions**: Reusable components
- **Tool Separation**: Debug tools isolated from production code

## 🎯 **Quick Start**

1. **Setup API** (one time):
   ```bash
   python main.py setup
   ```

2. **Analyze Replay**:
   ```bash
   python main.py analyze your_replay.wowsreplay
   ```

3. **Check Status**:
   ```bash
   python main.py status
   ```

## 📋 **File Summary**

| Directory | Files | Purpose |
|-----------|--------|---------|
| api/ | 3 files | API integration and configuration |
| core/ | 6 files | Replay processing and analysis |
| utils/ | 2 files | Utility functions and conversions |
| tools/ | 3 files | Debug and analysis utilities |
| Root | 4 files | Entry point, configuration, data, docs |

**Total: 18 files (down from 30+)**

---

**Clean, organized, and maintainable WoWS replay analysis system!** 🚢
