import unittest
from unittest.mock import Mock, patch

from job_hunt_agent import tracing


class ConfigurePhoenixTracingTest(unittest.TestCase):
    def tearDown(self) -> None:
        tracing._tracer_provider = None

    def test_configure_loads_dotenv_before_registering_phoenix(self) -> None:
        calls: list[str] = []
        fake_provider = object()

        def fake_load_dotenv() -> None:
            calls.append("dotenv")

        def fake_register(**kwargs: object) -> object:
            calls.append("register")
            return fake_provider

        with (
            patch.object(tracing, "_tracer_provider", None),
            patch.object(tracing, "_load_dotenv_if_available", side_effect=fake_load_dotenv),
            patch.object(tracing, "register", side_effect=fake_register),
            patch.object(tracing, "GoogleADKInstrumentor") as instrumentor,
            patch.object(tracing, "_instrument_google_genai_if_available") as genai_instrumentor,
        ):
            instrumentor.return_value.instrument = Mock()

            provider = tracing.configure_phoenix_tracing()

        self.assertIs(provider, fake_provider)
        self.assertEqual(calls, ["dotenv", "register"])
        instrumentor.return_value.instrument.assert_called_once_with(tracer_provider=fake_provider)
        genai_instrumentor.assert_called_once_with(fake_provider)


if __name__ == "__main__":
    unittest.main()
