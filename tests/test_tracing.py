import unittest
from unittest.mock import Mock, patch

from job_hunt_agent import tracing


class ConfigurePhoenixTracingTest(unittest.TestCase):
    def tearDown(self) -> None:
        tracing._tracer_provider = None

    def test_configure_loads_dotenv_before_registering_phoenix(self) -> None:
        calls: list[str] = []
        register_kwargs: dict[str, object] = {}
        fake_provider = object()

        def fake_load_dotenv() -> None:
            calls.append("dotenv")

        def fake_register(**kwargs: object) -> object:
            calls.append("register")
            register_kwargs.update(kwargs)
            return fake_provider

        with (
            patch.object(tracing, "_tracer_provider", None),
            patch.object(tracing, "_load_dotenv_if_available", side_effect=fake_load_dotenv),
            patch.object(tracing, "register", side_effect=fake_register),
        ):
            provider = tracing.configure_phoenix_tracing()

        self.assertIs(provider, fake_provider)
        self.assertEqual(calls, ["dotenv", "register"])
        self.assertTrue(register_kwargs["batch"])

    def test_flush_forces_batch_export(self) -> None:
        provider = Mock()
        provider.force_flush.return_value = True

        with patch.object(tracing, "_tracer_provider", provider):
            flushed = tracing.flush_phoenix_tracing(timeout_millis=321)

        self.assertTrue(flushed)
        provider.force_flush.assert_called_once_with(timeout_millis=321)


if __name__ == "__main__":
    unittest.main()
