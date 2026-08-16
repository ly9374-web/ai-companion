from typing import Type

from loguru import logger

from .stateless_llm.stateless_llm_interface import StatelessLLMInterface
from .stateless_llm.openai_compatible_llm import AsyncLLM as OpenAICompatibleLLM


class LLMFactory:
    @staticmethod
    def create_llm(llm_provider, **kwargs) -> Type[StatelessLLMInterface]:
        """Create an LLM based on the configuration.

        Args:
            llm_provider: The type of LLM to create
            **kwargs: Additional arguments
        """
        logger.info(f"Initializing LLM: {llm_provider}")

        if llm_provider in {"deepseek_llm", "grok_llm"}:
            return OpenAICompatibleLLM(
                model=kwargs.get("model"),
                base_url=kwargs.get("base_url"),
                llm_api_key=kwargs.get("llm_api_key"),
                organization_id=kwargs.get("organization_id"),
                project_id=kwargs.get("project_id"),
                temperature=kwargs.get("temperature"),
            )
        else:
            raise ValueError(f"Unsupported LLM provider: {llm_provider}")
