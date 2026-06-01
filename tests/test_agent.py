import unittest

from job_hunt_agent.agent import DEFAULT_MODEL, SYSTEM_INSTRUCTION, build_agent


class AgentDefinitionTest(unittest.TestCase):
    def test_build_agent_uses_job_hunt_identity(self) -> None:
        agent = build_agent()

        self.assertEqual(agent.name, "job_hunt_signal")
        self.assertEqual(agent.model, DEFAULT_MODEL)
        self.assertIn("Job Hunt Signal", agent.description)
        self.assertIn("referral targets", agent.description)

    def test_prompt_contains_v1_guardrails(self) -> None:
        required_phrases = [
            "Never invent job posting URLs",
            "Do not pretend you found live roles or people",
            "Avoid LinkedIn-influencer tone",
            "the user reviews and exports",
            "Always pass the user's resume text",
            "Assumptions:",
            "Tool-backed steps:",
        ]

        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, SYSTEM_INSTRUCTION)


if __name__ == "__main__":
    unittest.main()
