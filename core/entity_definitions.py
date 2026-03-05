#!/usr/bin/env python3
"""
entity_definitions.py
=====================
WoWS 15.1.0 entity type definitions and mappings.

Based on replay_unpack-master/clients/wows/versions/15_1_0 structure analysis.
"""

# Entity types from entity_defs/
ENTITY_TYPES = {
    "Account": {
        "description": "Player account data",
        "components": ["AccountInventoryAPIComponent", "UserDataComponent"],
        "data_fields": ["credits", "gold", "experience", "achievements"],
    },
    "Avatar": {
        "description": "Player avatar in game",
        "components": ["AccountReviverComponent", "BattleComponent"],
        "data_fields": ["player_id", "ship_id", "team", "status"],
    },
    "Vehicle": {
        "description": "Ship in battle",
        "components": ["BattleComponent", "RankedBattlesComponent"],
        "data_fields": ["hp", "max_hp", "damage_taken", "position", "yaw"],
    },
    "BattleEntity": {
        "description": "Base entity in battle",
        "components": ["BattleComponent", "MatchmakerComponent"],
        "data_fields": ["battle_type", "clock"],
    },
    "BattleLogic": {
        "description": "Battle logic controller",
        "components": ["BattleComponent", "BattlePassComponent"],
        "data_fields": ["is_winner", "teams", "statistics"],
    },
    "InteractiveObject": {
        "description": "Interactive world object (resources, objectives)",
        "components": ["EventHubComponent"],
        "data_fields": ["position", "type", "state"],
    },
    "InteractiveZone": {
        "description": "Zone for interactions (capture zones)",
        "components": ["EventHubComponent"],
        "data_fields": ["position", "radius", "owner", "points"],
    },
}

# User Data Objects from user_data_object_defs/
USER_DATA_OBJECTS = {
    "Ship": {
        "description": "Ship definition in battle",
        "properties": ["ship_id", "name", "tier", "nation", "hp", "position"],
    },
    "ControlPoint": {
        "description": "Control/capture point in map",
        "properties": ["position", "team", "points", "capture_progress"],
    },
    "SpawnPoint": {
        "description": "Ship spawn location",
        "properties": ["position", "team", "available"],
    },
    "Trigger": {
        "description": "Trigger volume for game events",
        "properties": ["position", "type", "radius"],
    },
    "MapBorder": {
        "description": "Map boundary",
        "properties": ["position"],
    },
    "Minefield": {
        "description": "Mine field region",
        "properties": ["position", "radius"],
    },
}

# Battle Components from component_defs/
BATTLE_COMPONENTS = {
    "BattleComponent": "Core battle state and logic",
    "MatchmakerComponent": "Matchmaking and team info",
    "RankedBattlesComponent": "Ranked battle specifics",
    "BrawlBattlesComponent": "Brawl mode specifics",
    "PVEBattlesComponent": "Co-op PVE specifics",
    "TrainingRoomComponent": "Training room data",
    "StatistAchievementsComponent": "Achievement tracking",
    "BattlePassComponent": "Battle pass progression",
    "VSEventComponent": "vs Event specifics",
    "SideChoiceEventComponent": "Side choice event",
    "GrandStrategyPassComponent": "Grand strategy pass",
    "StrategicActionsComponent": "Strategic actions",
    "ShipAcesComponent": "Ship aces system",
    "LootboxComponent": "Lootbox rewards",
    "EventHubComponent": "Event coordination",
}

# Interface types from entity_defs/interfaces/
INTERFACES = {
    "StatsOwner": "Entity owns statistics",
    "StatsPublisher": "Publishes stats to others",
    "TargetingOwner": "Entity has targeting system",
    "VisionOwner": "Entity has vision system",
    "AviationOwner": "Entity controls aircraft",
    "AirDefenceOwner": "Entity has air defense",
    "DamageDealerOwner": "Entity inflicts damage",
    "ModelOwner": "Entity has 3D model",
    "WalletOwner": "Entity has currency",
    "WeatherOwner": "Entity affects weather",
    "BuoyancyOwner": "Entity has buoyancy physics",
    "HitLocationManagerOwner": "Entity manages hit locations",
    "WritableEntity": "Entity data is writable",
}

# Map entity type to extractable data
ENTITY_DATA_MAPPING = {
    "Vehicle": {
        "battle_stats": ["damage_taken", "hits_taken", "fires", "torches"],
        "position_data": ["x", "y", "z", "yaw", "speed"],
        "state": ["hp", "is_alive", "team_id"],
    },
    "Avatar": {
        "player_info": ["account_id", "nickname", "clan"],
        "ship_data": ["ship_id", "ship_name", "ship_tier"],
        "session_data": ["kills", "xp_earned", "credits_earned"],
    },
    "InteractiveZone": {
        "control_data": ["team_id", "points", "progress"],
        "position_data": ["x", "y", "z"],
    },
    "ControlPoint": {
        "control_data": ["owner_team", "capture_progress"],
        "position_data": ["x", "y", "z"],
        "state": ["captured_at_clock"],
    },
}


def get_entity_type_info(entity_type: str) -> dict:
    """Get information about an entity type."""
    return ENTITY_TYPES.get(entity_type, {})


def get_udo_info(udo_type: str) -> dict:
    """Get information about a User Data Object."""
    return USER_DATA_OBJECTS.get(udo_type, {})


def get_extractable_fields(entity_type: str) -> list:
    """Get all extractable fields for an entity type."""
    return ENTITY_DATA_MAPPING.get(entity_type, {})


def is_battle_entity(entity_type: str) -> bool:
    """Check if entity type is battle-related."""
    return entity_type in ["Vehicle", "Avatar", "BattleEntity", "BattleLogic"]


def is_map_object(entity_type: str) -> bool:
    """Check if entity type is a map object."""
    return entity_type in ["InteractiveObject", "InteractiveZone", "ControlPoint"]


def list_all_entity_types() -> list:
    """List all known entity types."""
    return list(ENTITY_TYPES.keys())


def list_all_components() -> list:
    """List all battle components."""
    return list(BATTLE_COMPONENTS.keys())


def list_all_udos() -> list:
    """List all User Data Objects."""
    return list(USER_DATA_OBJECTS.keys())
