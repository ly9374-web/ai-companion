import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from open_llm_vtuber.current_relationship_score_manager import (
    CurrentRelationshipScoreManager,
)


class CurrentRelationshipScoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.manager = CurrentRelationshipScoreManager(history_root=self.root)

    async def _record_five(self, role):
        for index in range(5):
            ready = await self.manager.record_turn(
                role, "history", f"user {index}", f"assistant {index}"
            )
            self.assertEqual(ready, index == 4)

    async def test_five_turn_batch_uses_exact_formula_and_keeps_roles_separate(self):
        await self._record_five("algernon")
        calls = []

        async def score(turns):
            calls.append(turns)
            return '{"score":3}'

        self.assertTrue(
            await self.manager.summarize_pending_update("algernon", "history", score)
        )
        self.assertEqual(len(calls[0]), 5)
        self.assertAlmostEqual(self.manager.read_score("algernon"), 4.455)
        self.assertEqual(self.manager.read_score("cuige"), 3)
        self.assertEqual(
            len(self.manager._read_state("algernon")["pending_turns"]), 0
        )

    async def test_invalid_rating_keeps_batch_for_retry_and_negative_clamps_at_zero(self):
        await self._record_five("algernon")

        async def invalid(_turns):
            return '{"score":-6}'

        async def negative(_turns):
            return '{"score":-5}'

        self.assertFalse(
            await self.manager.summarize_pending_update("algernon", "history", invalid)
        )
        self.assertEqual(
            len(self.manager._read_state("algernon")["pending_turns"]), 5
        )
        self.assertTrue(
            await self.manager.summarize_pending_update("algernon", "history", negative)
        )
        self.assertEqual(self.manager.read_score("algernon"), 0)

    def test_rating_parser_rejects_non_integer_and_extra_fields(self):
        for value in ('{"score":2.0}', '{"score":true}', '{"score":2,"why":"x"}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.manager.parse_rating(value)

    def test_negative_rating_subtracts_its_magnitude(self):
        self.assertEqual(self.manager.apply_rating(54, -3), 51)
