import unittest
from pathlib import Path
import math

from core.replay_extract import extract_replay
from core.replay_unpack_adapter import TrackPoint, _sanitize_track, read_replay, decode_packets
from core.replay_schema import validate_extraction, to_legacy_schema
from renderers.minimap_renderer import _load_space_bin_world_bounds, _overview_half_extent, _world_bounds, _normalize_render_tracks, _render_layout, _layout_for_player_status, _find_death_times


ROOT = Path(__file__).resolve().parent.parent
SAMPLES = sorted(ROOT.glob("*.wowsreplay"))
SAMPLE = SAMPLES[0] if SAMPLES else None


class ReplayPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SAMPLE is None:
            raise unittest.SkipTest("No .wowsreplay sample found in repository root")

    def test_replay_reader_smoke(self):
        ctx = read_replay(str(SAMPLE))
        self.assertEqual(ctx.game, "wows")
        self.assertGreater(len(ctx.decrypted_data), 1000)
        self.assertIn("vehicles", ctx.engine_data)

    def test_packet_mapping_smoke(self):
        ctx = read_replay(str(SAMPLE))
        packets = decode_packets(ctx)
        names = {p.packet_name for p in packets}
        self.assertIn("Position", names)
        self.assertTrue("PlayerPosition" in names or "TYPE_43" in names or "TYPE_37" in names)

    def test_canonical_schema_validation(self):
        data = extract_replay(str(SAMPLE))
        result = validate_extraction(data)
        self.assertTrue(result.ok, msg="; ".join(result.errors))

    def test_legacy_adapter(self):
        canonical = extract_replay(str(SAMPLE))
        legacy = to_legacy_schema(canonical)
        self.assertIn("positions", legacy)
        self.assertIn("deaths", legacy)
        self.assertIn("battle_end", legacy)

    def test_integration_non_empty(self):
        data = extract_replay(str(SAMPLE))
        self.assertGreater(len(data.get("tracks", {})), 0)
        self.assertGreater(len(data.get("events", {}).get("deaths", [])), 0)
        self.assertGreater(len(data.get("diagnostics", {}).get("packet_counts", {})), 0)

    def test_battle_overlay_fields(self):
        data = extract_replay(str(SAMPLE))
        meta = data.get("meta", {}) or {}
        events = data.get("events", {}) or {}
        stats = data.get("stats", {}) or {}

        self.assertIsInstance(meta.get("control_points", []), list)
        self.assertIsInstance(events.get("captures", []), list)
        self.assertIsInstance(events.get("kills", []), list)
        self.assertIsInstance(events.get("health", []), list)
        self.assertIsInstance(events.get("player_status", []), list)
        self.assertIsInstance(stats.get("team_scores_final", {}), dict)
        self.assertIn("team_win_score", stats)

        captures = events.get("captures", [])
        if captures:
            snap = captures[0]
            self.assertIn("time_s", snap)
            self.assertIn("caps", snap)
            self.assertIn("team_scores", snap)

        health = events.get("health", [])
        if health:
            snap = health[0]
            self.assertIn("time_s", snap)
            self.assertIn("entities", snap)
            self.assertIsInstance(snap.get("entities"), dict)

        player_status = events.get("player_status", [])
        if player_status:
            snap = player_status[0]
            self.assertIn("time_s", snap)
            self.assertIn("damage_total", snap)
            self.assertIn("ribbons", snap)
            self.assertIn("ship_entity_key", snap)

    def test_account_team_alignment(self):
        data = extract_replay(str(SAMPLE))
        meta = data.get("meta", {}) or {}
        entities = data.get("entities", {}) or {}

        relation_by_account = {}
        for v in meta.get("vehicles", []) or []:
            account_id = v.get("id")
            relation = v.get("relation")
            if account_id is None or relation is None:
                continue
            relation_by_account[int(account_id)] = int(relation)

        self.assertGreater(len(relation_by_account), 0)

        mapped_accounts = []
        for entity in entities.values():
            account_id = entity.get("account_entity_id")
            if account_id is None:
                continue
            account_id = int(account_id)
            relation = relation_by_account.get(account_id)
            if relation is None:
                continue
            expected_team = "player" if relation == 0 else ("ally" if relation == 1 else "enemy")
            self.assertEqual(entity.get("team"), expected_team)
            mapped_accounts.append(account_id)

        self.assertGreater(len(mapped_accounts), 0)
        self.assertEqual(len(mapped_accounts), len(set(mapped_accounts)))

    def test_local_player_track_has_no_impossible_jumps(self):
        data = extract_replay(str(SAMPLE))
        player_name = str((data.get("meta", {}) or {}).get("playerName") or "").strip()
        self.assertTrue(player_name)

        player_track = None
        for track in (data.get("tracks", {}) or {}).values():
            if str(track.get("player_name") or "").strip() == player_name:
                player_track = track
                break

        self.assertIsNotNone(player_track)
        points = list((player_track or {}).get("points", []))
        self.assertGreater(len(points), 0)

        bad_jumps = []
        for a, b in zip(points, points[1:]):
            dt = float(b.get("t", 0.0)) - float(a.get("t", 0.0))
            if dt <= 0.0:
                continue
            dist = math.hypot(float(b.get("x", 0.0)) - float(a.get("x", 0.0)), float(b.get("z", 0.0)) - float(a.get("z", 0.0)))
            if dist > 35.0:
                bad_jumps.append((a.get("t"), b.get("t"), dist))

        self.assertEqual([], bad_jumps[:5], msg=f"unexpected local-player jumps: {bad_jumps[:5]}")

    def test_track_sanitizer_keeps_continuity_for_duplicate_timestamps(self):
        points = [
            TrackPoint(t=1.0, x=10.0, y=0.0, z=10.0, yaw=0.0),
            TrackPoint(t=2.0, x=20.0, y=0.0, z=20.0, yaw=0.0),
            TrackPoint(t=2.0, x=520.0, y=0.0, z=520.0, yaw=0.0),
            TrackPoint(t=3.0, x=30.0, y=0.0, z=30.0, yaw=0.0),
        ]

        sanitized = _sanitize_track(points)

        self.assertEqual(3, len(sanitized))
        self.assertEqual([1.0, 2.0, 3.0], [p.t for p in sanitized])
        self.assertAlmostEqual(20.0, sanitized[1].x)
        self.assertAlmostEqual(20.0, sanitized[1].z)

    def test_space_bin_bounds_parse_for_haven(self):
        bounds = _load_space_bin_world_bounds("50_Gold_harbor")
        self.assertIsNotNone(bounds)
        min_x, max_x, min_z, max_z = bounds or (0.0, 0.0, 0.0, 0.0)
        self.assertAlmostEqual(-835.3986, min_x, places=2)
        self.assertAlmostEqual(796.2786, max_x, places=2)
        self.assertAlmostEqual(-835.3986, min_z, places=2)
        self.assertAlmostEqual(796.2786, max_z, places=2)

    def test_overview_half_extent_for_haven(self):
        half = _overview_half_extent("50_Gold_harbor")
        self.assertIsNotNone(half)
        self.assertAlmostEqual(700.0, half or 0.0, places=3)

    def test_world_bounds_prefer_overview_size(self):
        data = extract_replay(str(SAMPLE))
        bounds = _world_bounds(data)
        self.assertEqual((-700.0, 700.0, -700.0, 700.0), tuple(float(v) for v in bounds))

    def test_player_layout_expands_for_extra_ribbon_rows(self):
        data = extract_replay(str(SAMPLE))
        render_tracks = _normalize_render_tracks(data)
        base_layout = _render_layout(render_tracks, 512)
        status = {
            "ribbons": {"15": 12, "14": 9, "17": 8, "8": 6, "3": 5, "0": 4, "1": 3, "6": 2},
        }
        dynamic_layout = _layout_for_player_status(base_layout, status)
        self.assertGreater(dynamic_layout["player_rect"][3], base_layout["player_rect"][3])
        self.assertGreater(dynamic_layout["feed_rect"][1], base_layout["feed_rect"][1])

    def test_find_death_times_uses_health_timeline(self):
        canonical = {
            "entities": {"42": {"death_time": None}},
            "events": {
                "deaths": [],
                "health": [
                    {"time_s": 10.0, "entities": {"42": {"hp": 1000, "alive": True}}},
                    {"time_s": 12.5, "entities": {"42": {"hp": 0, "alive": False}}},
                ],
            },
        }
        deaths = _find_death_times(canonical)
        self.assertEqual(12.5, deaths.get("42"))


if __name__ == "__main__":
    unittest.main()
