from dataclasses import dataclass
from typing import Any, Protocol

from openai import AsyncOpenAI

from .config import CompareConfig


class CompareError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ChatResponse:
    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ChatClient(Protocol):
    async def complete(self, messages: list[dict[str, Any]]) -> ChatResponse: ...


class QwenChatClient:
    def __init__(
        self, config: CompareConfig, client: AsyncOpenAI | None = None
    ) -> None:
        self.config = config
        self._owns_client = client is None
        self.client = client or AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.request_timeout,
            max_retries=config.max_retries,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.close()

    async def complete(self, messages: list[dict[str, Any]]) -> ChatResponse:
        response = await self.client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            max_tokens=self.config.max_output_tokens,
            temperature=self.config.temperature,
            extra_body={"max_pixels": self.config.max_pixels},
        )
        message = response.choices[0].message if response.choices else None
        text = message.content.strip() if message and message.content else ""
        refusal = getattr(message, "refusal", None) if message else None
        if refusal:
            raise CompareError(f"model refused the request: {refusal}")
        if not text:
            raise CompareError("model returned no text")
        usage = response.usage
        return ChatResponse(
            text=text,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
        )
