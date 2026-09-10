from __future__ import annotations

import asyncio

import pytest

from ai_kp.platform.agents.verifier import ConstrainedOutputVerifier


class StaticLlm:
    def __init__(self, response: str):
        self.response = response
        self.calls = 0

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.calls += 1
        return self.response


def test_deterministic_rejection_does_not_consult_model() -> None:
    llm = StaticLlm('{"accepted":true}')

    report = asyncio.run(
        ConstrainedOutputVerifier(llm).review(
            public_narration="门卫保持沉默。",
            required_content=("门卫明确拒绝。",),
            forbidden_disclosures=(),
            authority_summary={},
        )
    )

    assert report.accepted is False
    assert report.source == "deterministic"
    assert llm.calls == 0


def test_malformed_model_verdict_is_a_rejection_not_an_accepting_fallback() -> None:
    llm = StaticLlm('审查通过：{"accepted":true}')

    report = asyncio.run(
        ConstrainedOutputVerifier(llm).review(
            public_narration="门卫明确拒绝。",
            required_content=("门卫明确拒绝。",),
            forbidden_disclosures=(),
            authority_summary={},
        )
    )

    assert report.accepted is False
    assert report.source == "fallback"
    assert "确定性叙事" in report.patch_instruction


def test_model_cannot_control_verifier_report_provenance() -> None:
    llm = StaticLlm(
        '{"accepted":true,"missing":[],"contradictions":[],'
        '"patch_instruction":"","source":"model"}'
    )

    report = asyncio.run(
        ConstrainedOutputVerifier(llm).review(
            public_narration="门卫明确拒绝。",
            required_content=("门卫明确拒绝。",),
            forbidden_disclosures=(),
            authority_summary={},
        )
    )

    assert report.accepted is False
    assert report.source == "fallback"


@pytest.mark.parametrize(
    "response",
    [
        (
            '{"accepted":"true","missing":[],"contradictions":[],'
            '"patch_instruction":""}'
        ),
        (
            '{"accepted":true,"missing":[],"contradictions":[],'
            '"patch_instruction":"still needs repair"}'
        ),
        (
            '{"accepted":true,"missing":[],"contradictions":[],'
            '"patch_instruction":"","unexpected":1}'
        ),
    ],
)
def test_non_strict_or_internally_inconsistent_verdict_is_rejected(
    response: str,
) -> None:
    report = asyncio.run(
        ConstrainedOutputVerifier(StaticLlm(response)).review(
            public_narration="门卫明确拒绝。",
            required_content=("门卫明确拒绝。",),
            forbidden_disclosures=(),
            authority_summary={},
        )
    )

    assert report.accepted is False
    assert report.source == "fallback"


def test_strict_valid_model_verdict_can_accept() -> None:
    llm = StaticLlm(
        '{"accepted":true,"missing":[],"contradictions":[],'
        '"patch_instruction":""}'
    )

    report = asyncio.run(
        ConstrainedOutputVerifier(llm).review(
            public_narration="门卫明确拒绝。",
            required_content=("门卫明确拒绝。",),
            forbidden_disclosures=(),
            authority_summary={},
        )
    )

    assert report.accepted is True
    assert report.source == "model"
