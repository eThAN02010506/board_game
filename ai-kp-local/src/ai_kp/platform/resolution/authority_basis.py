"""Immutable evidence tying one kernel settlement to scenario authority."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class KernelAuthorityBasis(BaseModel):
    """Persisted fence shared by every deterministic kernel commit shape.

    ``preview_hash`` is the stable hash of the artifact being committed: an
    action preview for one action or a settlement preview for an atomic batch.
    Keeping the same basis for both paths prevents the transaction adapters
    from inventing different notions of current scenario authority.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["kernel-authority-basis.v1"] = (
        "kernel-authority-basis.v1"
    )
    run_id: str = Field(min_length=1, max_length=160)
    contract_version_id: str = Field(min_length=1, max_length=160)
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_version: int = Field(ge=0)
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    def require_artifact(
        self,
        *,
        run_id: str,
        state_version: int,
        preview_hash: str,
    ) -> None:
        """Reject a preview or settlement detached from this frozen basis."""

        if (
            self.run_id != run_id
            or self.state_version != state_version
            or self.preview_hash != preview_hash
        ):
            raise ValueError("Kernel authority basis does not match its preview")

    def require_current(
        self,
        *,
        contract_version_id: str,
        contract_hash: str,
        state_contract_version_id: str,
        state_version: int,
        snapshot_run_id: str,
    ) -> None:
        """Reject authority drift using persistence-neutral scalar values."""

        if (
            self.contract_version_id != contract_version_id
            or self.contract_hash != contract_hash
            or self.contract_version_id != state_contract_version_id
            or self.state_version != state_version
            or self.run_id != snapshot_run_id
        ):
            raise ValueError("Scenario authority changed; recompute the kernel preview")


__all__ = ["KernelAuthorityBasis"]
