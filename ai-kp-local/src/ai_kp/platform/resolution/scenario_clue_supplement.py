"""Constrained clue routes for source-coverage supplements."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_kp.platform.resolution.contracts import WorldCommand
from ai_kp.platform.resolution.scenario_ir_models import (
    IrAction,
    IrClue,
    ScenarioIrBatch,
)
from ai_kp.platform.resolution.source_coverage import SourceCoverageSupplementTarget


class CoverageClueProposal(BaseModel):
    """Source-grounded clue payload; the server owns identity and commands."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=240)
    public_content: str = Field(min_length=1, max_length=1200)


class CoverageClueEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    clues: tuple[CoverageClueProposal, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def reject_duplicate_clues(self) -> CoverageClueEnvelope:
        titles = [item.title.casefold() for item in self.clues]
        if len(titles) != len(set(titles)):
            raise ValueError("Coverage clue proposals must be unique")
        return self


_COVERAGE_CLUE_SCHEMA_JSON = json.dumps(
    CoverageClueEnvelope.model_json_schema(),
    ensure_ascii=False,
    separators=(",", ":"),
)


def coverage_clue_schema_json() -> str:
    return _COVERAGE_CLUE_SCHEMA_JSON


def supports_clue_materialization(
    targets: tuple[SourceCoverageSupplementTarget, ...],
) -> bool:
    return bool(targets) and all(
        target.requirement_key == "clue_path"
        and set(target.acceptable_record_kinds) == {"clues", "operators"}
        for target in targets
    )


def materialize_clue_envelope(
    envelope: CoverageClueEnvelope,
    *,
    source_block_id: str,
    record_id_prefix: str,
) -> ScenarioIrBatch:
    """Create a queryable clue and its executable discovery route."""

    actions: list[IrAction] = []
    clues: list[IrClue] = []
    for index, proposal in enumerate(envelope.clues, start=1):
        action_id = f"{record_id_prefix}discover_{index:02d}"
        clue_id = f"{record_id_prefix}clue_{index:02d}"
        digest = hashlib.sha256(f"{source_block_id}\0{clue_id}".encode()).hexdigest()
        fact_path = f"clues.discovered.{digest}"
        actions.append(
            IrAction(
                id=action_id,
                title=f"发现：{proposal.title}",
                intent_hints=(proposal.title[:160],),
                policy="automatic",
                automatic_information=(proposal.public_content,),
                always=(WorldCommand(kind="set_fact", path=fact_path, value=True),),
                source_block_ids=(source_block_id,),
            )
        )
        clues.append(
            IrClue(
                id=clue_id,
                title=proposal.title,
                importance="supporting",
                discovery_action_ids=(action_id,),
                fact_path=fact_path,
                fact_value=True,
                recoverable=True,
                public_content=(proposal.public_content,),
                source_block_ids=(source_block_id,),
            )
        )
    return ScenarioIrBatch(actions=tuple(actions), clues=tuple(clues), confidence="high")


__all__ = [
    "CoverageClueEnvelope",
    "coverage_clue_schema_json",
    "materialize_clue_envelope",
    "supports_clue_materialization",
]
