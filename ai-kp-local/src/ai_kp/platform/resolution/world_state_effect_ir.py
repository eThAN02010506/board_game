"""Small authoring records lowered into the shared world-state command."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from ai_kp.platform.resolution.contracts import WorldCommand

if TYPE_CHECKING:
    from ai_kp.platform.resolution.scenario_ir_models import IrAction


class IrWorldStateEffect(BaseModel):
    """Models choose values and timing, never a command kind or storage path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: str = Field(min_length=1, max_length=160)
    dimension: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    value: StrictStr | StrictInt | StrictBool | None
    visibility: Literal["table", "kp", "secret"]
    applies_on: Literal["always", "success", "failure", "pushed_failure"]

    @model_validator(mode="after")
    def require_supported_value(self) -> IrWorldStateEffect:
        # Reuse the actual command contract for bounds; no second value policy.
        self.to_command()
        return self

    def to_command(self) -> WorldCommand:
        return WorldCommand(
            kind="set_world_entity_state",
            entity_id=self.entity_id,
            path=self.dimension,
            value=self.value,
            payload={"visibility": self.visibility},
        )


def lower_world_state_effects(action: IrAction) -> IrAction:
    """Lower once before action analysis; reject overflow rather than lose effects."""
    if not action.world_state_effects:
        return action
    fields = {
        "always": "always",
        "success": "on_success",
        "failure": "on_failure",
        "pushed_failure": "on_pushed_failure",
    }
    updates = {field: list(getattr(action, field)) for field in fields.values()}
    for effect in action.world_state_effects:
        commands = updates[fields[effect.applies_on]]
        command = effect.to_command()
        if command.model_dump_json() not in {item.model_dump_json() for item in commands}:
            commands.append(command)
    for field, commands in updates.items():
        maximum = 8 if field == "on_pushed_failure" else 3
        if len(commands) > maximum:
            raise ValueError(
                f"Action {action.id} exceeds {field} command budget after state lowering"
            )
    return action.model_copy(
        update={
            **{field: tuple(commands) for field, commands in updates.items()},
            "world_state_effects": (),
        }
    )
