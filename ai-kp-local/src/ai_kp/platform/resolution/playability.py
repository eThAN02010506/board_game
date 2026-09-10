"""Source-agnostic causal playability proofs for compiled scenario contracts.

The analyzer deliberately understands only the closed contract DSL.  Scenario
names and prose never affect a result: models may propose a contract, while
this module proves whether its executable graph has usable branches, clue
delivery, recovery paths, and at least one terminal witness.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    ClueSpec,
    ScenarioContract,
    ScenarioSnapshot,
    StateCondition,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.playability_slicing import causal_proof_slice
from ai_kp.platform.resolution.world_settlement import WorldSettlementEngine

PlayabilityInvariant = Literal[
    "scene_reachability",
    "branch_consequences",
    "source_content_delivery",
    "failure_recovery",
    "core_clue_discoverability",
    "ending_reachability",
]
ProofStatus = Literal["passed", "failed", "indeterminate"]


class PlayabilityProof(BaseModel):
    """Auditable witness or counterexample for one release invariant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    invariant: PlayabilityInvariant
    status: ProofStatus
    witness: tuple[str, ...] = ()
    counterexamples: tuple[str, ...] = ()


class PlayabilityReport(BaseModel):
    """Deterministic release-readiness result kept beside schema validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ready: bool = False
    explored_state_count: int = Field(default=0, ge=0)
    exploration_complete: bool = False
    reachable_scene_ids: tuple[str, ...] = ()
    proofs: tuple[PlayabilityProof, ...] = ()

    def proof(self, invariant: PlayabilityInvariant) -> PlayabilityProof:
        return next(item for item in self.proofs if item.invariant == invariant)


@dataclass(frozen=True)
class _Transition:
    source: int
    target: int
    label: str
    operator_id: str | None = None
    outcome: str | None = None


@dataclass
class _Exploration:
    kernel: ActionResolutionKernel
    states: list[ScenarioSnapshot]
    witnesses: list[tuple[str, ...]]
    transitions: list[_Transition]
    reached_scenes: dict[str, tuple[str, ...]]
    reached_operator_outcomes: dict[tuple[str, str], tuple[str, ...]]
    ending_witnesses: dict[str, tuple[str, ...]]
    ending_state_ids: set[int]
    complete: bool


@dataclass(frozen=True)
class ReachableOutcomeExploration:
    """Bounded outcome catalog with an explicit completeness witness."""

    outcomes: frozenset[tuple[str, str]]
    explored_state_count: int
    complete: bool


class ScenarioPlayabilityAnalyzer:
    """Perform bounded exact-state exploration over the authoritative DSL."""

    def __init__(self, *, maximum_states: int = 10_000):
        if maximum_states < 1:
            raise ValueError("maximum_states must be positive")
        self.maximum_states = maximum_states

    def analyze(self, contract: ScenarioContract) -> PlayabilityReport:
        ending_paths = {
            condition.path
            for ending in contract.endings
            for condition in (*ending.all_conditions, *ending.any_conditions)
        }
        scene_exploration = self._proof_explore(contract, {"scene_id"})
        ending_exploration = self._proof_explore(contract, ending_paths)
        clue_explorations = {
            clue.clue_id: self._proof_explore(
                contract,
                {
                    f"facts.{clue.fact_path.removeprefix('facts.')}",
                    *ending_paths,
                },
                target_operator_ids=set(clue.discovery_operator_ids),
            )
            for clue in contract.clues
        }
        explorations = [scene_exploration, ending_exploration]
        explorations.extend(clue_explorations.values())
        proofs = (
            self._scene_proof(contract, scene_exploration),
            self._branch_proof(contract),
            self._content_proof(contract, clue_explorations),
            self._recovery_proof(contract, clue_explorations),
            self._clue_proof(contract, clue_explorations),
            self._ending_proof(contract, ending_exploration),
        )
        complete = all(item.complete for item in explorations)
        return PlayabilityReport(
            ready=complete and all(item.status == "passed" for item in proofs),
            # This is the peak exact-state working set, not cumulative work.
            # Each proof is independently bounded by ``maximum_states``.
            explored_state_count=max(len(item.states) for item in explorations),
            exploration_complete=complete,
            reachable_scene_ids=tuple(sorted(scene_exploration.reached_scenes)),
            proofs=proofs,
        )

    def reachable_operator_outcomes(
        self, contract: ScenarioContract
    ) -> frozenset[tuple[str, str]]:
        """Expose only outcomes witnessed by the same bounded state explorer."""

        return self.reachable_operator_outcome_exploration(contract).outcomes

    def reachable_operator_outcome_exploration(
        self, contract: ScenarioContract
    ) -> ReachableOutcomeExploration:
        """Return witnesses plus whether the configured state bound was exhaustive."""

        exploration = self._explore(contract)
        return ReachableOutcomeExploration(
            outcomes=frozenset(exploration.reached_operator_outcomes),
            explored_state_count=len(exploration.states),
            complete=exploration.complete,
        )

    def _scene_proof(
        self, contract: ScenarioContract, exploration: _Exploration
    ) -> PlayabilityProof:
        if not contract.locations:
            return PlayabilityProof(
                invariant="scene_reachability",
                status="passed",
                witness=("Contract has no location graph.",),
            )
        missing = sorted(
            item.location_id
            for item in contract.locations
            if item.location_id not in exploration.reached_scenes
        )
        if missing:
            return PlayabilityProof(
                invariant="scene_reachability",
                status="indeterminate" if not exploration.complete else "failed",
                witness=tuple(
                    f"{scene}: {' -> '.join(path) or 'initial state'}"
                    for scene, path in sorted(exploration.reached_scenes.items())
                ),
                counterexamples=tuple(
                    f"location {location_id} has no executable path from initial_scene_id"
                    for location_id in missing
                ),
            )
        return PlayabilityProof(
            invariant="scene_reachability",
            status="passed",
            witness=tuple(
                f"{scene}: {' -> '.join(path) or 'initial state'}"
                for scene, path in sorted(exploration.reached_scenes.items())
            ),
        )

    def _branch_proof(self, contract: ScenarioContract) -> PlayabilityProof:
        failures: list[str] = []
        witnesses: list[str] = []
        for operator in contract.operators:
            if not operator.skill_choices:
                continue
            success = self._outcome_signature(operator, "success")
            failure = self._outcome_signature(operator, "failure")
            if not self._outcome_observable(operator, "success"):
                failures.append(f"{operator.operator_id}.success has no observable result")
            if not self._outcome_observable(operator, "failure"):
                failures.append(f"{operator.operator_id}.failure has no observable result")
            if not operator.failure_commands:
                failures.append(
                    f"{operator.operator_id}.failure has no authoritative state consequence"
                )
            if success == failure:
                failures.append(
                    f"{operator.operator_id} success and failure have identical effects"
                )
            missing_failure_stakes = [
                choice.skill_key
                for choice in operator.skill_choices
                if not choice.failure_stakes.strip()
            ]
            if missing_failure_stakes:
                failures.append(
                    f"{operator.operator_id} does not disclose ordinary failure stakes for "
                    + ", ".join(missing_failure_stakes)
                )
            pushable = [choice for choice in operator.skill_choices if choice.allow_push]
            pushed_branch = next(
                (
                    branch
                    for branch in operator.outcome_branches
                    if branch.outcome_key == "pushed_failure"
                ),
                None,
            )
            if pushable and pushed_branch is None:
                failures.append(
                    f"{operator.operator_id} permits pushing without an authoritative "
                    "pushed_failure branch"
                )
            if pushable and pushed_branch is not None and not pushed_branch.commands:
                failures.append(
                    f"{operator.operator_id}.pushed_failure has no authoritative "
                    "state consequence"
                )
            missing_push_stakes = [
                choice.skill_key
                for choice in pushable
                if not choice.pushed_failure_stakes.strip()
            ]
            if missing_push_stakes:
                failures.append(
                    f"{operator.operator_id} does not disclose pushed-failure stakes for "
                    + ", ".join(missing_push_stakes)
                )
            if not any(item.startswith(f"{operator.operator_id}.") for item in failures):
                witnesses.append(f"{operator.operator_id}: success/failure branches differ")
        return PlayabilityProof(
            invariant="branch_consequences",
            status="failed" if failures else "passed",
            witness=tuple(witnesses) or ("No checked operators require branch proof.",),
            counterexamples=tuple(failures),
        )

    def _content_proof(
        self,
        contract: ScenarioContract,
        explorations: Mapping[str, _Exploration],
    ) -> PlayabilityProof:
        operators = {item.operator_id: item for item in contract.operators}
        failures: list[str] = []
        witnesses: list[str] = []
        indeterminate = False
        for clue in contract.clues:
            exploration = explorations[clue.clue_id]
            reliable = []
            for operator_id in clue.discovery_operator_ids:
                operator = operators[operator_id]
                if (
                    self._commits_clue(operator, clue, "success")
                    and clue.public_content
                    and all(
                        item in self._success_content(operator, contract)
                        for item in clue.public_content
                    )
                    and (operator_id, "success")
                    in exploration.reached_operator_outcomes
                ):
                    reliable.append(operator_id)
            if not reliable:
                indeterminate = indeterminate or not exploration.complete
                failures.append(
                    f"clue {clue.clue_id} has no reachable success branch that both "
                    "commits the clue and delivers concrete public content"
                )
            else:
                witnesses.append(f"{clue.clue_id}: delivered by {', '.join(reliable)}")
        return PlayabilityProof(
            invariant="source_content_delivery",
            status=(
                "indeterminate" if failures and indeterminate
                else "failed" if failures else "passed"
            ),
            witness=tuple(witnesses) or ("No clues require content delivery.",),
            counterexamples=tuple(failures),
        )

    def _clue_proof(
        self,
        contract: ScenarioContract,
        explorations: Mapping[str, _Exploration],
    ) -> PlayabilityProof:
        failures: list[str] = []
        witnesses: list[str] = []
        indeterminate = False
        for clue in self._core_clues(contract):
            exploration = explorations[clue.clue_id]
            paths = [
                exploration.reached_operator_outcomes[(operator_id, "success")]
                for operator_id in clue.discovery_operator_ids
                if (operator_id, "success") in exploration.reached_operator_outcomes
                and self._commits_clue(
                    next(
                        item
                        for item in contract.operators
                        if item.operator_id == operator_id
                    ),
                    clue,
                    "success",
                )
            ]
            if not paths:
                indeterminate = indeterminate or not exploration.complete
                failures.append(f"core clue {clue.clue_id} has no executable discovery route")
            else:
                witnesses.append(f"{clue.clue_id}: {' -> '.join(min(paths, key=len))}")
        return PlayabilityProof(
            invariant="core_clue_discoverability",
            status=(
                "indeterminate" if failures and indeterminate
                else "failed" if failures else "passed"
            ),
            witness=tuple(witnesses) or ("No core clues require discovery proof.",),
            counterexamples=tuple(failures),
        )

    def _recovery_proof(
        self,
        contract: ScenarioContract,
        explorations: Mapping[str, _Exploration],
    ) -> PlayabilityProof:
        failures: list[str] = []
        witnesses: list[str] = []
        indeterminate = False
        for clue in self._core_clues(contract):
            exploration = explorations[clue.clue_id]
            kernel = exploration.kernel
            adjacency: dict[int, list[_Transition]] = {}
            for transition in exploration.transitions:
                adjacency.setdefault(transition.source, []).append(transition)
            route_ids = set(clue.discovery_operator_ids)
            relevant = [
                edge
                for edge in exploration.transitions
                if edge.operator_id in route_ids
                and edge.outcome in {"failure", "pushed_failure"}
            ]
            for edge in relevant:
                recovered = self._recovery_from_state(
                    clue,
                    edge.target,
                    exploration,
                    adjacency,
                    kernel,
                )
                if recovered is None:
                    indeterminate = indeterminate or not exploration.complete
                    failures.append(
                        f"{edge.operator_id}.{edge.outcome} can strand core clue "
                        f"{clue.clue_id} without recovery, alternative route, or ending"
                    )
                else:
                    witnesses.append(
                        f"{edge.operator_id}.{edge.outcome} -> {' -> '.join(recovered)}"
                    )
        return PlayabilityProof(
            invariant="failure_recovery",
            status=(
                "indeterminate" if failures and indeterminate
                else "failed" if failures else "passed"
            ),
            witness=tuple(dict.fromkeys(witnesses))
            or ("No core-clue failure branch requires recovery proof.",),
            counterexamples=tuple(dict.fromkeys(failures)),
        )

    def _ending_proof(
        self, contract: ScenarioContract, exploration: _Exploration
    ) -> PlayabilityProof:
        premature = sorted(
            ending_id
            for ending_id, witness in exploration.ending_witnesses.items()
            if not witness
        )
        if premature:
            return PlayabilityProof(
                invariant="ending_reachability",
                status="failed",
                counterexamples=tuple(
                    f"ending {ending_id} is already satisfied in the initial state"
                    for ending_id in premature
                ),
            )
        if exploration.ending_witnesses:
            return PlayabilityProof(
                invariant="ending_reachability",
                status="passed",
                witness=tuple(
                    f"{ending}: {' -> '.join(path) or 'initial state'}"
                    for ending, path in sorted(exploration.ending_witnesses.items())
                ),
            )
        return PlayabilityProof(
            invariant="ending_reachability",
            status="indeterminate" if not exploration.complete else "failed",
            counterexamples=(
                "No ending condition is reachable from the initial authoritative state.",
            ),
        )

    def _proof_explore(
        self,
        contract: ScenarioContract,
        seed_paths: set[str],
        *,
        target_operator_ids: set[str] | None = None,
    ) -> _Exploration:
        proof_slice = causal_proof_slice(
            contract,
            seed_paths,
            target_operator_ids=target_operator_ids or set(),
        )
        if proof_slice is None:
            # Unknown event-ledger reads or control mutations cannot be safely
            # projected. Retain the old exact global proof and its fail-closed
            # state bound instead of guessing.
            return self._explore(contract)
        return self._explore(
            contract,
            relevant_paths=proof_slice.state_paths,
            operator_ids=proof_slice.operator_ids,
        )

    def _explore(
        self,
        contract: ScenarioContract,
        *,
        relevant_paths: frozenset[str] | None = None,
        operator_ids: frozenset[str] | None = None,
    ) -> _Exploration:
        initial = contract.initial_snapshot(f"playability:{contract.contract_id}")
        settlement = WorldSettlementEngine(contract)
        kernel = settlement.kernel
        if relevant_paths is None:
            relevant_paths = self._relevant_state_paths(contract)
        once_trigger_ids = frozenset(
            rule.trigger_id for rule in contract.trigger_rules if rule.once_per_run
        )
        states = [initial]
        witnesses: list[tuple[str, ...]] = [()]
        by_key = {
            self._state_key(initial, relevant_paths, kernel, once_trigger_ids): 0
        }
        pending = deque([0])
        transitions: list[_Transition] = []
        reached_scenes: dict[str, tuple[str, ...]] = {}
        reached_outcomes: dict[tuple[str, str], tuple[str, ...]] = {}
        ending_witnesses: dict[str, tuple[str, ...]] = {}
        ending_state_ids: set[int] = set()
        complete = True

        while pending:
            state_id = pending.popleft()
            state = states[state_id]
            witness = witnesses[state_id]
            scene = state.scene_id
            if isinstance(scene, str):
                reached_scenes.setdefault(scene, witness)
            matched_ending = state.status == "completed"
            if state.status == "completed" and state.ending_id:
                ending_witnesses.setdefault(state.ending_id, witness)
                ending_state_ids.add(state_id)
            elif state.status != "completed":
                # Initial-state matches are a release defect even though the
                # runtime evaluates endings only after a non-empty command batch.
                for ending in contract.endings:
                    if kernel.conditions_satisfied(state, ending.all_conditions) and (
                        not ending.any_conditions
                        or any(
                            kernel.conditions_satisfied(state, (condition,))
                            for condition in ending.any_conditions
                        )
                    ):
                        ending_witnesses.setdefault(ending.ending_id, witness)
                        ending_state_ids.add(state_id)
                        matched_ending = True
            if matched_ending:
                # The runtime marks the run completed before accepting another
                # action.  Do not manufacture recovery or scene paths beyond a
                # terminal state during static exploration.
                continue

            candidates: list[
                tuple[str, ScenarioSnapshot, str | None, str | None]
            ] = []
            for operator in contract.operators:
                if operator_ids is not None and operator.operator_id not in operator_ids:
                    continue
                if operator.policy in {"impossible", "clarification"} or not (
                    kernel.conditions_satisfied(state, operator.preconditions)
                ):
                    continue
                for outcome, commands in self._operator_outcomes(operator):
                    label = f"{operator.operator_id}.{outcome}"
                    action_commands = (
                        WorldCommand(
                            kind="emit_event",
                            event_type="action_resolved",
                            payload={
                                "action_id": f"playability:{label}",
                                "operator_id": operator.operator_id,
                                "outcome": outcome,
                            },
                        ),
                        *operator.always_commands,
                        *commands,
                    )
                    try:
                        _, resulting = settlement.settle(state, action_commands)
                    except ValueError:
                        # Runtime rejects the same out-of-bounds or otherwise
                        # invalid atomic batch; it is not an executable edge.
                        continue
                    candidates.append((label, resulting, operator.operator_id, outcome))
            for label, resulting, operator_id, outcome in candidates:
                key = self._state_key(
                    resulting, relevant_paths, kernel, once_trigger_ids
                )
                target = by_key.get(key)
                if target is None:
                    if len(states) >= self.maximum_states:
                        complete = False
                        pending.clear()
                        break
                    target = len(states)
                    by_key[key] = target
                    states.append(resulting)
                    witnesses.append((*witness, label))
                    pending.append(target)
                transitions.append(
                    _Transition(
                        source=state_id,
                        target=target,
                        label=label,
                        operator_id=operator_id,
                        outcome=outcome,
                    )
                )
                if operator_id is not None and outcome is not None:
                    reached_outcomes.setdefault(
                        (operator_id, outcome), (*witness, label)
                    )
            if not complete:
                break
        return _Exploration(
            kernel=kernel,
            states=states,
            witnesses=witnesses,
            transitions=transitions,
            reached_scenes=reached_scenes,
            reached_operator_outcomes=reached_outcomes,
            ending_witnesses=ending_witnesses,
            ending_state_ids=ending_state_ids,
            complete=complete,
        )

    @staticmethod
    def _operator_outcomes(
        operator: ActionOperator,
    ) -> tuple[tuple[str, tuple[WorldCommand, ...]], ...]:
        if operator.skill_choices:
            standard = (
                ("success", operator.success_commands),
                ("failure", operator.failure_commands),
            )
        else:
            standard = (("success", operator.success_commands),)
        return (*standard, *((item.outcome_key, item.commands) for item in operator.outcome_branches))


    @staticmethod
    def _relevant_state_paths(contract: ScenarioContract) -> frozenset[str]:
        """Project exact states onto fields that can affect the six proofs.

        Outcome ledgers and narrative-only facts remain in runtime snapshots, but
        they must not create an exponential static state product unless a
        precondition, clue, reactive rule, location route, or ending reads them.
        Equal projections are therefore equivalent for every future transition
        and release invariant evaluated by this analyzer.
        """

        paths = {
            "scene_id",
            *(f"resources.{item.resource_id}" for item in contract.resources),
            *(f"clocks.{item.clock_id}" for item in contract.clocks),
        }

        def include_conditions(conditions: Sequence[StateCondition]) -> None:
            paths.update(item.path for item in conditions)

        for operator in contract.operators:
            include_conditions(operator.preconditions)
        for link in contract.location_links:
            include_conditions(link.preconditions)
        for ending in contract.endings:
            include_conditions(ending.all_conditions)
            include_conditions(ending.any_conditions)
        # Supporting/optional clue facts do not participate in any current
        # release invariant by themselves. Their discovery outcome and public
        # content are witnessed on edges; only core clue state is needed by the
        # failure-recovery proof. Any clue fact explicitly read by a condition
        # is already included above and therefore remains in the projection.
        for clue in ScenarioPlayabilityAnalyzer._core_clues(contract):
            paths.add(f"facts.{clue.fact_path.removeprefix('facts.')}")
        for policy in contract.reactive_policies:
            for rule in policy.rules:
                include_conditions(rule.conditions)
        for rule in contract.trigger_rules:
            include_conditions(rule.conditions)
        for pressure in contract.pressure_tracks:
            # Threshold crossings are implicit reads performed by the shared
            # settlement engine, not ordinary StateCondition records.
            paths.add(f"clocks.{pressure.clock_id}")
        return frozenset(paths)

    @staticmethod
    def _state_key(
        state: ScenarioSnapshot,
        relevant_paths: frozenset[str],
        kernel: ActionResolutionKernel,
        once_trigger_ids: frozenset[str],
    ) -> tuple[tuple[str, str], ...]:
        query = kernel.query_snapshot(state)
        values = []
        for path in sorted(relevant_paths):
            exists, value = query.state_value(path)
            values.append(
                (
                    path,
                    json.dumps(
                        value if exists else {"$missing": True},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            )
        # WorldRuleEngine persists once-only control state as semantic markers.
        # Project only their stable identifiers; retaining the complete event
        # ledger would make repeated ordinary actions grow the graph forever.
        marker_set: set[tuple[str, str]] = set()
        for event in state.events:
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            marker = ScenarioPlayabilityAnalyzer._future_eligibility_marker(
                event, payload, once_trigger_ids
            )
            if marker is not None:
                marker_set.add(marker)
        markers = sorted(marker_set)
        values.append(
            (
                "$world_rule_markers",
                json.dumps(markers, ensure_ascii=False, separators=(",", ":")),
            )
        )
        return tuple(values)

    @staticmethod
    def _future_eligibility_marker(
        event: Mapping[str, object],
        payload: Mapping[str, object],
        once_trigger_ids: frozenset[str],
    ) -> tuple[str, str] | None:
        event_type = str(event.get("type") or "")
        if event_type == "pressure_stage_reached":
            stage_id = str(payload.get("pressure_stage_id") or "")
            return (event_type, stage_id) if stage_id else None
        if event_type == "trigger_fired":
            trigger_id = str(payload.get("trigger_id") or "")
            if trigger_id in once_trigger_ids:
                return event_type, trigger_id
        return None

    @staticmethod
    def _outcome_signature(operator: ActionOperator, outcome: str) -> tuple[str, ...]:
        if outcome == "success":
            commands = operator.success_commands
        elif outcome == "failure":
            commands = operator.failure_commands
        else:
            branch = next(
                (item for item in operator.outcome_branches if item.outcome_key == outcome),
                None,
            )
            commands = branch.commands if branch is not None else ()
        cues = tuple(
            cue.public_summary
            for cue in operator.narrative_cues
            if cue.outcome_key == outcome
        )
        return (
            *(command.model_dump_json() for command in operator.always_commands),
            *(command.model_dump_json() for command in commands),
            *cues,
            *(operator.automatic_information if outcome == "success" else ()),
        )

    @staticmethod
    def _outcome_observable(operator: ActionOperator, outcome: str) -> bool:
        if outcome == "success":
            commands = operator.success_commands
            stakes: Iterable[str] = operator.automatic_information
        else:
            commands = operator.failure_commands
            stakes = (
                choice.failure_stakes
                for choice in operator.skill_choices
                if choice.failure_stakes
            )
        return bool(
            operator.always_commands
            or commands
            or tuple(stakes)
            or operator.response_obligation_ids
            or any(cue.outcome_key == outcome for cue in operator.narrative_cues)
        )

    @staticmethod
    def _success_content(
        operator: ActionOperator, contract: ScenarioContract
    ) -> tuple[str, ...]:
        content = list(operator.automatic_information)
        obligations = {
            item.obligation_id: item for item in contract.response_obligations
        }
        for obligation_id in operator.response_obligation_ids:
            obligation = obligations[obligation_id]
            content.extend(obligation.facts_to_convey)
            content.extend(obligation.automatic_information)
        content.extend(
            cue.public_summary
            for cue in operator.narrative_cues
            if cue.outcome_key == "success"
        )
        return tuple(item for item in content if item.strip())

    @staticmethod
    def _core_clues(contract: ScenarioContract) -> tuple[ClueSpec, ...]:
        return tuple(item for item in contract.clues if item.importance == "core")

    @staticmethod
    def _commits_clue(
        operator: ActionOperator, clue: ClueSpec, outcome: str
    ) -> bool:
        if outcome == "success":
            commands = (*operator.always_commands, *operator.success_commands)
        elif outcome == "failure":
            commands = (*operator.always_commands, *operator.failure_commands)
        else:
            branch = next(
                (item for item in operator.outcome_branches if item.outcome_key == outcome),
                None,
            )
            commands = (
                (*operator.always_commands, *branch.commands)
                if branch is not None
                else operator.always_commands
            )
        expected_path = clue.fact_path.removeprefix("facts.")
        return any(
            command.kind == "set_fact"
            and command.path == expected_path
            and command.value == clue.fact_value
            for command in commands
        )

    def _recovery_from_state(
        self,
        clue: ClueSpec,
        start: int,
        exploration: _Exploration,
        adjacency: Mapping[int, list[_Transition]],
        kernel: ActionResolutionKernel,
    ) -> tuple[str, ...] | None:
        expected_path = f"facts.{clue.fact_path.removeprefix('facts.')}"
        pending = deque([(start, ())])
        visited = {start}
        while pending:
            state_id, witness = pending.popleft()
            state = exploration.states[state_id]
            found, value = kernel.state_value(state, expected_path)
            if found and value == clue.fact_value:
                return witness or ("clue already committed with a cost",)
            if state_id in exploration.ending_state_ids:
                return witness or ("explicit terminal result",)
            for edge in adjacency.get(state_id, ()):
                if edge.target in visited:
                    continue
                visited.add(edge.target)
                pending.append((edge.target, (*witness, edge.label)))
        return None


__all__ = [
    "PlayabilityProof",
    "PlayabilityReport",
    "ScenarioPlayabilityAnalyzer",
]
