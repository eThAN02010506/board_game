from dataclasses import dataclass
from enum import StrEnum


class HumanKpCommandType(StrEnum):
    APPROVE = "approve"
    OVERRIDE = "override"
    HIDE = "hide"
    REVEAL = "reveal"
    FREEZE_AI = "freeze_ai"


@dataclass(frozen=True)
class HumanKpCommand:
    command_type: HumanKpCommandType
    campaign_id: str
    target_id: str | None = None
    note: str = ""

