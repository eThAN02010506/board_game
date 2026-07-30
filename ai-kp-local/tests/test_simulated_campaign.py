import unittest

from ai_kp.evaluation.simulated_campaign import run_simulation


class SimulatedCampaignTests(unittest.TestCase):
    def test_replay_is_deterministic_and_secret_requires_reveal(self) -> None:
        definition = {
            "steps": [
                {"action": "emit", "audience": "kp", "content": "凶手是管家"},
                {"action": "assert_player_not_sees", "text": "凶手"},
                {"action": "reveal", "text": "凶手"},
                {"action": "assert_player_sees", "text": "凶手"},
            ]
        }
        first = run_simulation(definition)
        second = run_simulation(definition)
        self.assertEqual(first["status"], "passed")
        self.assertEqual(first["result_fingerprint"], second["result_fingerprint"])
        self.assertEqual(first["metrics"]["passed_assertions"], 2)


if __name__ == "__main__":
    unittest.main()
