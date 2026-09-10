"""Ruleset-neutral text guards shared by authoritative narrative boundaries."""

from __future__ import annotations

import re

_SUCCESS_CLAIM_PATTERNS = (
    "发现了",
    "发现",
    "找到了",
    "找到",
    "获得了",
    "拿到",
    "取得",
    "成功",
    "达成了",
    "说服",
    "相信",
    "同意",
    "愿意",
    "让步",
    "吓走",
    "击退",
    "打开了",
)
_NEGATION_PREFIXES = ("没有", "没", "未", "无法", "未能", "并未", "不曾", "并不")


def narration_claims_success(narration: str) -> bool:
    """Detect an affirmative success claim while preserving explicit negation."""

    for word in _SUCCESS_CLAIM_PATTERNS:
        for match in re.finditer(re.escape(word), narration):
            start = match.start()
            if start == 0:
                return True
            preceding = narration[max(0, start - 3) : start]
            if any(preceding.endswith(negation) for negation in _NEGATION_PREFIXES):
                continue
            return True
    return False


__all__ = ["narration_claims_success"]
