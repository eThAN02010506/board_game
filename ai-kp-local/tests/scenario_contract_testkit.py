"""Shared builders for source-bound scenario-contract test fixtures."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from ai_kp.platform.resolution.authority_basis import KernelAuthorityBasis
from ai_kp.platform.resolution.contracts import ResolutionPreview

_SOURCED_GROUPS = (
    "locations",
    "location_links",
    "entities",
    "clocks",
    "resources",
    "clues",
    "operators",
    "task_methods",
    "reactive_policies",
    "response_obligations",
    "trigger_rules",
    "pressure_tracks",
    "consequence_signals",
    "endings",
)


def source_bound_payload(
    payload: Mapping[str, Any],
    *,
    source_block_id: str = "fixture-source",
    document_id: str = "fixture-document",
) -> dict[str, Any]:
    """Return a detached fixture whose executable records cite one source block."""

    result = deepcopy(dict(payload))
    source_ref = {
        "source_block_id": source_block_id,
        "document_id": document_id,
    }
    for group in _SOURCED_GROUPS:
        for record in result.get(group, []):
            if not record.get("source_refs"):
                record["source_refs"] = [dict(source_ref)]
    return result


def without_source_refs(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached negative fixture with all executable provenance removed."""

    result = deepcopy(dict(payload))
    for group in _SOURCED_GROUPS:
        for record in result.get(group, []):
            record["source_refs"] = []
    return result


def bind_payload_to_module(
    repo: Any,
    module_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind every fixture record to one real extracted block in the test module."""

    module = repo.get_module(module_id)
    chunks = repo.list_module_chunks(
        module_id,
        allowed_visibility=("player", "table", "kp", "secret"),
        spoiler_tags=None,
    )
    if not chunks:
        raise ValueError("Test module has no source block")
    result = deepcopy(dict(payload))
    source_ref = {
        "source_block_id": str(chunks[0]["id"]),
        "document_id": str(module.get("source_hash") or module["id"]),
        "page": chunks[0].get("page_start"),
        "paragraph": chunks[0].get("paragraph_start"),
    }
    for group in _SOURCED_GROUPS:
        for record in result.get(group, []):
            record["source_refs"] = [dict(source_ref)]
    return result


def kernel_authority_basis(
    repo: Any,
    run_id: str,
    preview: ResolutionPreview,
) -> KernelAuthorityBasis:
    """Capture the same persisted authority tuple required by action commits."""

    binding = repo.get_module_run_contract_binding(run_id)
    state = repo.get_scenario_run_state(run_id)
    return KernelAuthorityBasis(
        run_id=run_id,
        contract_version_id=str(binding["contract_version_id"]),
        contract_hash=str(binding["contract_hash"]),
        state_version=int(state["state_version"]),
        preview_hash=preview.preview_hash,
    )


__all__ = [
    "bind_payload_to_module",
    "kernel_authority_basis",
    "source_bound_payload",
    "without_source_refs",
]
