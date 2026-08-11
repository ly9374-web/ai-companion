import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from open_llm_vtuber.config_manager.utils import read_yaml, validate_config
from open_llm_vtuber.service_context import ServiceContext
from open_llm_vtuber.websocket_handler import WebSocketHandler
from run_server import ensure_port_is_available


class _RecordingAgent:
    def __init__(self):
        self.system_prompts = []

    def set_system(self, system_prompt: str) -> None:
        self.system_prompts.append(system_prompt)


class SystemPromptRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_updates_active_agent_when_prompt_changes(self):
        context = ServiceContext()
        context.character_config = validate_config(
            read_yaml(str(PROJECT_ROOT / "conf.yaml"))
        ).character_config
        context.live2d_model = SimpleNamespace(emo_str="[smile]")
        context.agent_engine = _RecordingAgent()
        context.persona_prompt = "old template"
        context.system_prompt = "old rendered prompt"

        with patch(
            "open_llm_vtuber.service_context.prompt_builder.resolve_persona_prompt",
            return_value="fresh template: {emomap_keys}",
        ):
            changed = await context.refresh_character_system_prompt()
            unchanged = await context.refresh_character_system_prompt()

        self.assertTrue(changed)
        self.assertFalse(unchanged)
        self.assertEqual(context.persona_prompt, "fresh template: {emomap_keys}")
        self.assertEqual(context.system_prompt, "fresh template: [smile]")
        self.assertEqual(
            context.agent_engine.system_prompts,
            ["fresh template: [smile]"],
        )

    async def test_new_websocket_context_does_not_share_default_agent(self):
        config = validate_config(read_yaml(str(PROJECT_ROOT / "conf.yaml")))
        shared_agent = object()
        default_context = SimpleNamespace(
            config=config,
            system_config=config.system_config,
            character_config=config.character_config,
            live2d_model=object(),
            asr_engine=object(),
            tts_engine=object(),
            vad_engine=None,
            agent_engine=shared_agent,
            mcp_server_registery=None,
            tool_adapter=None,
        )
        handler = WebSocketHandler(default_context)

        with patch.object(ServiceContext, "load_cache", new=AsyncMock()) as load:
            await handler._init_service_context(AsyncMock(), "client-1")

        self.assertIsNone(load.await_args.kwargs["agent_engine"])
        self.assertIsNot(load.await_args.kwargs["agent_engine"], shared_agent)


class ServerStartupSafetyTests(unittest.TestCase):
    def test_port_check_rejects_an_existing_listener(self):
        probe = MagicMock()
        probe.__enter__.return_value = probe
        probe.connect_ex.return_value = 0
        with patch(
            "run_server.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("127.0.0.1", 12393))],
        ), patch("run_server.socket.socket", return_value=probe):
            with self.assertRaisesRegex(RuntimeError, "already in use"):
                ensure_port_is_available("127.0.0.1", 12393)


if __name__ == "__main__":
    unittest.main()
