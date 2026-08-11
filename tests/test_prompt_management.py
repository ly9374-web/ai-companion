import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from prompts import prompt_builder, prompt_loader
from open_llm_vtuber.config_manager.character import CharacterConfig
from open_llm_vtuber.config_manager.utils import read_yaml, validate_config


class PromptLoaderTests(unittest.TestCase):
    def test_prompts_yaml_is_the_only_prompt_content_file(self):
        prompt_root = PROJECT_ROOT / "prompts"
        self.assertTrue((prompt_root / "prompts.yaml").is_file())
        self.assertEqual(list(prompt_root.rglob("*.txt")), [])

        data = read_yaml(str(prompt_root / "prompts.yaml"))
        self.assertTrue(
            {"chat", "summaries", "system", "utility", "tools", "runtime"}
            .issubset(data)
        )

    def test_yaml_prompts_are_not_cached(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            prompt_file = Path(temp_dir) / "prompts.yaml"
            prompt_file.write_text("runtime:\n  sample: first\n", encoding="utf-8")
            with patch.object(prompt_loader, "PROMPT_FILE", prompt_file):
                self.assertEqual(prompt_loader.load_prompt("runtime.sample"), "first")
                prompt_file.write_text(
                    "runtime:\n  sample: second\n", encoding="utf-8"
                )
                self.assertEqual(
                    prompt_loader.load_prompt("runtime.sample"), "second"
                )

    def test_render_reports_missing_values_and_rejects_invalid_keys(self):
        with self.assertRaisesRegex(ValueError, "Missing template value"):
            prompt_loader.render_prompt("chat.contexts.clipboard")
        with self.assertRaisesRegex(ValueError, "Invalid prompt key"):
            prompt_loader.load_prompt("../conf")
        with self.assertRaisesRegex(KeyError, "Prompt key not found"):
            prompt_loader.load_prompt("runtime.does_not_exist")

    def test_dynamic_yaml_templates_render_with_named_values(self):
        cases = {
            "chat.contexts.clipboard": {"content": "sample"},
            "chat.contexts.long_term_memory": {"memories": "- sample"},
            "chat.contexts.long_term_relationship": {
                "relationship_file": "{}"
            },
            "chat.contexts.short_term_relationship": {
                "relationship_file": "{}"
            },
        }
        for key, values in cases.items():
            with self.subTest(prompt=key):
                self.assertTrue(
                    prompt_loader.render_prompt(key, **values).strip()
                )
        self.assertIn(
            "smile",
            prompt_loader.render_util(
                "live2d_expression_prompt", emomap_keys="smile"
            ),
        )


class PromptBuilderTests(unittest.TestCase):
    def test_complete_chat_user_prompt_shows_all_injection_positions(self):
        request = prompt_builder.build_user_request(
            text_prompt="你好",
            long_term_memory_context="长期记忆背景",
            long_term_relationship_context="长期关系背景",
            short_term_relationship_context="短期关系背景",
            has_images=False,
        )

        self.assertLess(request.index("长期记忆背景"), request.index("长期关系背景"))
        self.assertLess(request.index("长期关系背景"), request.index("短期关系背景"))
        self.assertIn("[本轮用户输入]\n你好", request)

    def test_all_summary_user_prompts_are_yaml_driven_valid_json(self):
        memory_payload = json.loads(
            prompt_builder.build_long_term_memory_summary_input(
                [{"记忆命名": "偏好", "记忆内容": "喜欢咖啡"}],
                [{"user": "用户说话", "assistant": "角色回答"}],
            )
        )
        long_payload = json.loads(
            prompt_builder.build_long_term_relationship_summary_input(
                "长期记忆原文", "长期关系原文"
            )
        )
        short_payload = json.loads(
            prompt_builder.build_short_term_relationship_summary_input(
                [{"user": "用户说话", "assistant": "角色回答"}],
                "长期关系原文",
                "短期关系原文",
            )
        )

        self.assertEqual(memory_payload["最近三轮对话"][0]["user"], "用户说话")
        self.assertEqual(
            long_payload["long_term_memory.md全部内容"], "长期记忆原文"
        )
        self.assertEqual(
            short_payload["现有short_term_relationship.md全部内容"],
            "短期关系原文",
        )

    def test_mcp_prompt_uses_yaml_templates(self):
        prompt = prompt_builder.build_mcp_prompt(
            {
                "weather": {
                    "forecast": {
                        "description": "Get a forecast",
                        "parameters": {
                            "city": {
                                "type": "string",
                                "description": "City name",
                            }
                        },
                        "required": ["city"],
                    }
                }
            }
        )
        self.assertIn("MCP Tools Capability Section", prompt)
        self.assertIn("服务器：weather", prompt)
        self.assertIn("forecast", prompt)
        self.assertIn("描述：Get a forecast", prompt)
        self.assertIn("必填：city", prompt)


class CharacterSystemPromptTests(unittest.TestCase):
    def setUp(self):
        self.config_data = read_yaml(str(PROJECT_ROOT / "conf.yaml"))

    def test_algernon_and_mili_have_correct_complete_system_prompts(self):
        config = validate_config(copy.deepcopy(self.config_data))
        algernon_template = prompt_builder.resolve_persona_prompt(
            config.character_config.persona_prompt_file,
            config.character_config.persona_prompt,
        )
        algernon = prompt_builder.render_character_system_prompt(
            algernon_template, "smile, curious"
        )
        self.assertTrue(algernon.startswith("你是一个友善害羞的人工智能 Algernon"))
        self.assertNotIn("mili", algernon)
        self.assertIn("smile, curious", algernon)
        self.assertIn("为自己困在虚拟世界感到无聊", algernon)
        self.assertIn("# 对话规范", algernon)

        alt = read_yaml(str(PROJECT_ROOT / "characters" / "mili.yaml"))[
            "character_config"
        ]
        merged = config.character_config.model_dump()
        merged.update(alt)
        mili_config = validate_config(
            {
                "system_config": config.system_config.model_dump(),
                "character_config": merged,
            }
        )
        mili_template = prompt_builder.resolve_persona_prompt(
            mili_config.character_config.persona_prompt_file,
            mili_config.character_config.persona_prompt,
        )
        mili = prompt_builder.render_character_system_prompt(
            mili_template, "smile, shy"
        )
        self.assertEqual(mili_config.character_config.character_name, "Mili")
        self.assertTrue(mili.startswith("你是 Mili，一个刚刚被创造出来的人工智能"))
        self.assertNotIn("你是 Algernon", mili)
        self.assertIn("smile, shy", mili)
        self.assertIn("今天是你来到这个世界的第一天", mili)
        self.assertIn("# 对话规范", mili)

    def test_legacy_inline_persona_remains_supported(self):
        legacy = copy.deepcopy(self.config_data)
        character = legacy["character_config"]
        character.pop("persona_prompt_file", None)
        character["persona_prompt"] = "Legacy inline persona"

        config = validate_config(legacy)
        self.assertEqual(
            prompt_builder.resolve_persona_prompt(
                config.character_config.persona_prompt_file,
                config.character_config.persona_prompt,
            ),
            "Legacy inline persona",
        )

    def test_persona_source_is_required(self):
        valid = validate_config(copy.deepcopy(self.config_data))
        character = valid.character_config.model_dump()
        character["persona_prompt_file"] = None
        character["persona_prompt"] = ""

        with self.assertRaises(ValidationError):
            CharacterConfig.model_validate(character)


if __name__ == "__main__":
    unittest.main()
