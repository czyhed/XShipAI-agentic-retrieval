from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.request import Request, urlopen

from .config import Settings


class AnswerGenerator(Protocol):
    def generate(self, query: str, context_pack: list[dict[str, Any]], profile: str) -> dict[str, Any]: ...


class DisabledAnswerGenerator:
    def generate(self, query: str, context_pack: list[dict[str, Any]], profile: str) -> dict[str, Any]:
        return {"status": "failed", "reason": "answer provider unavailable"}


class OpenAICompatibleAnswerGenerator:
    def __init__(self, settings: Settings):
        self.settings = settings

    def generate(self, query: str, context_pack: list[dict[str, Any]], profile: str) -> dict[str, Any]:
        if not self.settings.answer_base_url or not self.settings.answer_api_key:
            return {"status": "failed", "reason": "answer provider credentials are not configured"}
        endpoint = self.settings.answer_base_url.rstrip("/") + "/chat/completions"
        citation_ids = [str(item.get("citationId")) for item in context_pack if item.get("citationId")]
        prompt = {
            "query": query,
            "contextPack": context_pack,
            "instructions": (
                "Answer in Chinese using only the supplied contextPack. "
                "If any context item supports the answer, set abstain to false and answer directly. "
                "citationIds must contain one or more IDs copied verbatim from allowedCitationIds; "
                "never invent IDs or use chunkId/documentId. "
                "Set abstain to true only when the context contains no support for the query. "
                "Return only a JSON object with keys answer, citationIds, claims, and abstain."
            ),
            "allowedCitationIds": citation_ids,
            "outputSchema": {
                "answer": "string",
                "citationIds": ["S1"],
                "claims": [],
                "abstain": False,
            },
        }
        body = json.dumps(
            {
                "model": self.settings.answer_model,
                "messages": [
                    {"role": "system", "content": "You are a grounded answer generator. Use only the provided context and allowed citation IDs. Never invent citations or source locations."},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        request = Request(endpoint, data=body, headers={"Authorization": f"Bearer {self.settings.answer_api_key}", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.settings.answer_timeout_ms / 1000) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            result = json.loads(content) if isinstance(content, str) else content
            if not isinstance(result, dict):
                raise ValueError("answer response is not an object")
            citation_ids = result.get("citationIds") or result.get("citations") or []
            if not isinstance(citation_ids, list):
                raise ValueError("answer citations are not a list")
            return {
                "status": "sent",
                "answer": result.get("answer"),
                "citationIds": [str(value) for value in citation_ids],
                "claims": result.get("claims") if isinstance(result.get("claims"), list) else [],
                "abstain": bool(result.get("abstain")),
            }
        except Exception:
            return {"status": "failed", "reason": "answer provider request failed"}


def build_answer_generator(settings: Settings) -> AnswerGenerator:
    if settings.answer_provider == "openai-compatible":
        return OpenAICompatibleAnswerGenerator(settings)
    return DisabledAnswerGenerator()
