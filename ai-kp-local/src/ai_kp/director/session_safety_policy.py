"""Inject identity-free Session 0 safety policy into every campaign AI call."""

import contextvars
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from ai_kp.platform.ports.llm import ChatMessage, LlmClient

_campaign_scope: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ai_kp_safety_campaign", default=None
)


class CampaignSafetyPolicyLlm:
    """A transparent LLM decorator; raw private preferences never leave SQLite."""

    def __init__(self, connection: sqlite3.Connection | None, inner: LlmClient):
        self.connection = connection
        self.inner = inner

    @contextmanager
    def bind_campaign(self, campaign_id: str) -> Iterator[None]:
        token = _campaign_scope.set(campaign_id)
        try:
            yield
        finally:
            _campaign_scope.reset(token)

    async def complete(
        self, messages: list[ChatMessage], temperature: float = 0.7
    ) -> str:
        campaign_id = _campaign_scope.get()
        policy = (
            self._load_policy(campaign_id)
            if campaign_id and self.connection is not None
            else None
        )
        if policy is not None:
            messages = [
                ChatMessage(
                    role="system",
                    content=(
                        "以下是本团匿名聚合的当前内容安全政策；协议修订若仍在全桌"
                        "重新确认中，也必须先按更严格的边界执行。"
                        "Lines 不得生成或推进；Veils 只能淡出处理，不描写细节；"
                        "不得猜测、记录或暴露是哪位成员提出边界。\n"
                        f"{json.dumps(policy, ensure_ascii=False, sort_keys=True)}"
                    ),
                ),
                *messages,
            ]
        return await self.inner.complete(messages, temperature=temperature)

    def _load_policy(self, campaign_id: str) -> dict | None:
        revision = self.connection.execute(
            """
            SELECT id, config_json FROM campaign_setup_revisions
            WHERE campaign_id = ? AND status IN ('pending', 'active')
            ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, version DESC
            LIMIT 1
            """,
            (campaign_id,),
        ).fetchone()
        if revision is None:
            return None
        config = json.loads(revision["config_json"])
        lines = list(config.get("lines") or ())
        veils = list(config.get("veils") or ())
        for row in self.connection.execute(
            "SELECT private_json FROM session_zero_preferences WHERE revision_id = ?",
            (revision["id"],),
        ).fetchall():
            private = json.loads(row["private_json"])
            lines.extend(private.get("lines") or ())
            veils.extend(private.get("veils") or ())
        unique = lambda values: list(
            dict.fromkeys(str(value).strip() for value in values if str(value).strip())
        )
        policy = {
            "content_warnings": unique(config.get("content_warnings") or ()),
            "lines": unique(lines),
            "veils": unique(veils),
            "safety_default": config.get("safety_default", "pause"),
        }
        if not policy["content_warnings"] and not policy["lines"] and not policy["veils"]:
            return None
        return policy


__all__ = ["CampaignSafetyPolicyLlm"]
