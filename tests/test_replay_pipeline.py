import unittest
from pathlib import Path

from core.replay_extract import extract_replay
from core.replay_unpack_adapter import read_replay, decode_packets
from core.replay_schema import validate_extraction, to_legacy_schema


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


if __name__ == "__main__":
    unittest.main()
