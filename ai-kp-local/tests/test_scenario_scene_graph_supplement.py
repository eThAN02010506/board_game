from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    EndingRule,
    LocationLink,
    LocationSpec,
    ScenarioContract,
    SourceRef,
    StateCondition,
)
from ai_kp.platform.resolution.evidence_compiler import EvidenceBoundContractCandidate
from ai_kp.platform.resolution.location_travel import canonical_travel_operator_id
from ai_kp.platform.resolution.scenario_authoring import (
    ConstrainedScenarioContractAuthoringAdapter,
    ScenarioAuthoringEvidence,
    ScenarioContractReview,
)
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from ai_kp.platform.resolution.scenario_scene_graph_supplement import (
    SceneGraphLinkSelection,
    SceneGraphSourceContext,
    SceneGraphSupplementEnvelope,
    apply_scene_graph_envelope,
    retain_source_grounded_location_links,
    scene_graph_location_catalog,
)


def test_rejects_unknown_slots_and_sources_instead_of_inventing_graph() -> None:
    opening = SourceRef(source_block_id="opening", document_id="doc")
    contract = ScenarioContract(
        contract_id="safe",
        source_version=1,
        ruleset_id="coc7",
        title="Safe",
        locations=(
            LocationSpec(location_id="street", title="Street", source_refs=(opening,)),
            LocationSpec(location_id="house", title="House", source_refs=(opening,)),
        ),
    )
    envelope = SceneGraphSupplementEnvelope(
        links=(
            SceneGraphLinkSelection(
                from_slot=0, to_slot=2, source_block_ids=("route",)
            ),
        )
    )
    with pytest.raises(ValueError, match="Unknown scene-graph location slot"):
        apply_scene_graph_envelope(
            envelope,
            contract=contract,
            source_refs={"opening": opening, "route": opening},
            source_texts={"opening": "Opening.", "route": "Route."},
        )

    envelope = SceneGraphSupplementEnvelope(
        initial_scene_slot=0,
        initial_scene_source_block_ids=("invented",),
    )
    with pytest.raises(ValueError, match="outside its catalog"):
        apply_scene_graph_envelope(
            envelope,
            contract=contract,
            source_refs={"opening": opening},
            source_texts={"opening": "The investigators begin on the street."},
        )


def test_initial_scene_requires_citation_and_self_links_are_invalid() -> None:
    with pytest.raises(ValidationError, match="requires source evidence"):
        SceneGraphSupplementEnvelope(initial_scene_slot=0)
    with pytest.raises(ValidationError, match="two different locations"):
        SceneGraphLinkSelection(
            from_slot=1, to_slot=1, source_block_ids=("route",)
        )


def test_envelope_rejects_duplicate_bidirectional_route_selections() -> None:
    with pytest.raises(ValidationError, match="bidirectional scene route"):
        SceneGraphSupplementEnvelope(
            links=(
                SceneGraphLinkSelection(
                    from_slot=1,
                    to_slot=2,
                    one_way=False,
                    source_block_ids=("forward",),
                ),
                SceneGraphLinkSelection(
                    from_slot=2,
                    to_slot=1,
                    one_way=False,
                    source_block_ids=("reverse",),
                ),
            )
        )


def test_envelope_allows_opposite_distinct_one_way_routes() -> None:
    envelope = SceneGraphSupplementEnvelope(
        links=(
            SceneGraphLinkSelection(
                from_slot=1,
                to_slot=2,
                one_way=True,
                source_block_ids=("forward",),
            ),
            SceneGraphLinkSelection(
                from_slot=2,
                to_slot=1,
                one_way=True,
                source_block_ids=("reverse",),
            ),
        )
    )

    assert len(envelope.links) == 2


def test_applies_additive_repair_after_assembly_and_keeps_destination_hidden() -> None:
    opening = SourceRef(source_block_id="opening", document_id="doc", paragraph=1)
    route = SourceRef(source_block_id="route", document_id="doc", paragraph=2)
    contract = ScenarioContract(
        contract_id="haunting",
        source_version=1,
        ruleset_id="coc7",
        title="The Haunting",
        locations=(
            LocationSpec(
                location_id="street", title="Street", source_refs=(opening,)
            ),
            LocationSpec(location_id="house", title="House", source_refs=(route,)),
        ),
    )

    repaired = apply_scene_graph_envelope(
        SceneGraphSupplementEnvelope(
            initial_scene_slot=0,
            initial_scene_source_block_ids=("opening",),
            links=(
                SceneGraphLinkSelection(
                    from_slot=0, to_slot=1, source_block_ids=("route",)
                ),
            ),
        ),
        contract=contract,
        source_refs={"opening": opening, "route": route},
        source_texts={
            "opening": "The investigators begin on the street.",
            "route": "The street leads to the house.",
        },
    )

    assert repaired.initial_scene_id == "street"
    assert repaired.locations[0].initial_visibility == "visited"
    assert repaired.locations[1].initial_visibility == "hidden"
    assert repaired.location_links[0].source_refs == (route,)
    assert contract.initial_scene_id is None
    assert [item.model_dump(mode="json") for item in scene_graph_location_catalog(repaired)] == [
        {"slot": 0, "title": "Street", "source_block_ids": ["opening"]},
        {"slot": 1, "title": "House", "source_block_ids": ["route"]},
    ]


def test_initial_scene_accepts_structured_opening_heading_and_position_body() -> None:
    heading = SourceRef(
        source_block_id="opening-heading", document_id="doc", page=9
    )
    body = SourceRef(source_block_id="opening-body", document_id="doc", page=9)
    contract = ScenarioContract(
        contract_id="structured-opening",
        source_version=1,
        ruleset_id="coc7",
        title="Structured opening",
        locations=(
            LocationSpec(location_id="room", title="Room", source_refs=(body,)),
        ),
    )
    contexts = {
        source_id: SceneGraphSourceContext(
            title="Opening Scene",
            section_path=("Introduction", "Opening Scene"),
        )
        for source_id in ("opening-heading", "opening-body")
    }

    repaired = apply_scene_graph_envelope(
        SceneGraphSupplementEnvelope(
            initial_scene_slot=0,
            initial_scene_source_block_ids=("opening-heading", "opening-body"),
        ),
        contract=contract,
        source_refs={"opening-heading": heading, "opening-body": body},
        source_texts={
            "opening-heading": "Opening Scene",
            "opening-body": "You have all gathered outside Gardiner's room.",
        },
        source_contexts=contexts,
    )

    assert repaired.initial_scene_id == "room"


def test_structured_opening_accepts_bounded_possessive_title_inversion() -> None:
    opening = SourceRef(source_block_id="opening", document_id="doc", page=9)
    contract = ScenarioContract(
        contract_id="possessive-opening",
        source_version=1,
        ruleset_id="coc7",
        title="Possessive opening",
        locations=(
            LocationSpec(
                location_id="gardiner-room",
                title="Gardiner's Room",
                source_refs=(opening,),
            ),
        ),
    )

    repaired = apply_scene_graph_envelope(
        SceneGraphSupplementEnvelope(
            initial_scene_slot=0,
            initial_scene_source_block_ids=("opening",),
        ),
        contract=contract,
        source_refs={"opening": opening},
        source_texts={
            "opening": "You have all gathered outside the room of Mr. James Gardiner."
        },
        source_contexts={
            "opening": SceneGraphSourceContext(title="Opening Scene")
        },
    )

    assert repaired.initial_scene_id == "gardiner-room"


@pytest.mark.parametrize(
    ("body_text", "body_section"),
    (
        ("You want to investigate the Room later.", "Opening Scene"),
        ("You have all gathered outside the Room.", "Background"),
        ("You have all gathered in a room near Gardiner.", "Opening Scene"),
    ),
)
def test_structured_opening_proof_rejects_nonpositional_or_unscoped_evidence(
    body_text: str,
    body_section: str,
) -> None:
    contract = ScenarioContract(
        contract_id="invalid-structured-opening",
        source_version=1,
        ruleset_id="coc7",
        title="Invalid structured opening",
        locations=(
            LocationSpec(location_id="room", title="Gardiner's Room"),
        ),
    )
    refs = {
        "heading": SourceRef(
            source_block_id="heading", document_id="other-doc", page=20
        ),
        "body": SourceRef(source_block_id="body", document_id="doc", page=9),
    }

    with pytest.raises(ValueError, match="initial scene is not directly entailed"):
        apply_scene_graph_envelope(
            SceneGraphSupplementEnvelope(
                initial_scene_slot=0,
                initial_scene_source_block_ids=("heading", "body"),
            ),
            contract=contract,
            source_refs=refs,
            source_texts={"heading": "Opening Scene", "body": body_text},
            source_contexts={
                "heading": SceneGraphSourceContext(title="Opening Scene"),
                "body": SceneGraphSourceContext(title=body_section),
            },
        )


def test_rejects_mentions_conditional_routes_and_objectives_as_topology_proof() -> None:
    opening = SourceRef(source_block_id="opening", document_id="doc")
    route = SourceRef(source_block_id="route", document_id="doc")
    contract = ScenarioContract(
        contract_id="fail-closed",
        source_version=1,
        ruleset_id="coc7",
        title="Fail closed",
        locations=(
            LocationSpec(location_id="street", title="Street", source_refs=(opening,)),
            LocationSpec(location_id="house", title="House", source_refs=(route,)),
        ),
    )

    with pytest.raises(ValueError, match="initial scene is not directly entailed"):
        apply_scene_graph_envelope(
            SceneGraphSupplementEnvelope(
                initial_scene_slot=1,
                initial_scene_source_block_ids=("opening",),
            ),
            contract=contract,
            source_refs={"opening": opening, "route": route},
            source_texts={
                "opening": "The investigators are hired to investigate the House.",
                "route": "The Street and House are described in this chapter.",
            },
        )

    with pytest.raises(ValueError, match="route endpoints are not directly entailed"):
        apply_scene_graph_envelope(
            SceneGraphSupplementEnvelope(
                links=(
                    SceneGraphLinkSelection(
                        from_slot=0,
                        to_slot=1,
                        source_block_ids=("route",),
                    ),
                ),
            ),
            contract=contract,
            source_refs={"opening": opening, "route": route},
            source_texts={
                "opening": "The investigators begin on the Street.",
                "route": "If the investigators unlock the Street door, it leads to the House.",
            },
        )


def test_equal_titles_cannot_entail_a_route_between_duplicate_slots() -> None:
    route = SourceRef(source_block_id="route", document_id="doc")
    contract = ScenarioContract(
        contract_id="duplicates",
        source_version=1,
        ruleset_id="coc7",
        title="Duplicates",
        locations=(
            LocationSpec(location_id="house-a", title="House", source_refs=(route,)),
            LocationSpec(location_id="house-b", title="House", source_refs=(route,)),
        ),
    )

    with pytest.raises(ValueError, match="route endpoints are not directly entailed"):
        apply_scene_graph_envelope(
            SceneGraphSupplementEnvelope(
                links=(
                    SceneGraphLinkSelection(
                        from_slot=0,
                        to_slot=1,
                        source_block_ids=("route",),
                    ),
                ),
            ),
            contract=contract,
            source_refs={"route": route},
            source_texts={"route": "The House leads to the House."},
        )


def test_base_links_cross_the_same_source_entailment_boundary() -> None:
    direct = SourceRef(source_block_id="direct", document_id="doc")
    objective = SourceRef(source_block_id="objective", document_id="doc")
    secret = SourceRef(source_block_id="secret", document_id="doc")
    locations = (
        LocationSpec(location_id="street", title="Street"),
        LocationSpec(location_id="house", title="House"),
        LocationSpec(location_id="cellar", title="Cellar"),
    )
    contract = ScenarioContract(
        contract_id="base-links",
        source_version=1,
        ruleset_id="coc7",
        title="Base links",
        locations=locations,
        location_links=(
            LocationLink(
                from_location_id="street",
                to_location_id="house",
                source_refs=(direct,),
            ),
            LocationLink(
                from_location_id="house",
                to_location_id="cellar",
                source_refs=(objective,),
            ),
            LocationLink(
                from_location_id="house",
                to_location_id="cellar",
                one_way=True,
                preconditions=(
                    StateCondition(path="facts.cellar_unlocked", operator="eq", value=True),
                ),
                source_refs=(secret,),
            ),
        ),
    )

    retained = retain_source_grounded_location_links(
        contract,
        source_texts={
            "direct": "The Street leads directly to the House.",
            "objective": "The investigators are hired to investigate the House and Cellar.",
            "secret": "If the hidden panel is unlocked, the House passage leads to the Cellar.",
        },
    )

    assert tuple((item.from_location_id, item.to_location_id) for item in retained.location_links) == (
        ("street", "house"),
        ("house", "cellar"),
    )
    assert retained.location_links[1].preconditions


def test_base_links_do_not_treat_destination_choice_as_adjacency() -> None:
    choices = SourceRef(source_block_id="choices", document_id="doc")
    corridor = SourceRef(source_block_id="corridor", document_id="doc")
    contract = ScenarioContract(
        contract_id="investigation-options",
        source_version=1,
        ruleset_id="coc7",
        title="Investigation options",
        locations=(
            LocationSpec(location_id="globe", title="环球报社"),
            LocationSpec(location_id="library", title="中央图书馆"),
            LocationSpec(location_id="archive", title="档案馆"),
        ),
        location_links=(
            LocationLink(
                from_location_id="globe",
                to_location_id="library",
                source_refs=(choices,),
            ),
            LocationLink(
                from_location_id="library",
                to_location_id="archive",
                source_refs=(corridor,),
            ),
        ),
    )

    retained = retain_source_grounded_location_links(
        contract,
        source_texts={
            "choices": "调查员可以去环球报社，也可以去中央图书馆，由你们决定。",
            "corridor": "中央图书馆通过地下走廊连接档案馆。",
        },
    )

    assert tuple(
        (item.from_location_id, item.to_location_id)
        for item in retained.location_links
    ) == (("library", "archive"),)


class TopologyLlm:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.calls += 1
        if "开场地点选择 Agent" in messages[0].content:
            return json.dumps({
                "initial_scene_slot": self.payload.get("initial_scene_slot"),
                "source_block_ids": self.payload.get(
                    "initial_scene_source_block_ids", []
                ),
            })
        return json.dumps({
            "initial_scene_slot": None,
            "initial_scene_source_block_ids": [],
            "links": self.payload.get("links", []),
        })


class BatchedTopologyLlm:
    def __init__(self) -> None:
        self.user_prompts: list[str] = []

    async def complete(self, messages, temperature: float = 0.7) -> str:
        prompt = messages[-1].content
        self.user_prompts.append(prompt)
        if "开场地点选择 Agent" in messages[0].content:
            if '"source_block_id":"opening"' in prompt:
                return json.dumps({
                    "initial_scene_slot": 0,
                    "source_block_ids": ["opening"],
                })
            return json.dumps({
                "initial_scene_slot": None,
                "source_block_ids": [],
            })
        if '"source_block_id":"opening"' in prompt:
            return json.dumps({
                "initial_scene_slot": None,
                "initial_scene_source_block_ids": [],
                "links": [],
            })
        if '"source_block_id":"route-last"' in prompt:
            return json.dumps({
                "initial_scene_slot": None,
                "initial_scene_source_block_ids": [],
                "links": [{
                    "from_slot": 0,
                    "to_slot": 1,
                    "one_way": False,
                    "source_block_ids": ["route-last"],
                }],
            })
        return json.dumps({
            "initial_scene_slot": None,
            "initial_scene_source_block_ids": [],
            "links": [],
        })


class RetryingTopologyLlm:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.prompts.append(messages[-1].content)
        source_id = "invented-route" if len(self.prompts) == 1 else "route"
        return json.dumps({
            "initial_scene_slot": None,
            "initial_scene_source_block_ids": [],
            "links": [{
                "from_slot": 0,
                "to_slot": 1,
                "one_way": False,
                "source_block_ids": [source_id],
            }],
        })


class SchemaRetryingTopologyLlm(RetryingTopologyLlm):
    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.prompts.append(messages[-1].content)
        if len(self.prompts) == 1:
            return json.dumps({
                "initial_scene_slot": None,
                "wrong_initial_source_field": [],
                "links": [],
            })
        return json.dumps({
            "initial_scene_slot": None,
            "initial_scene_source_block_ids": [],
            "links": [{
                "from_slot": 0,
                "to_slot": 1,
                "one_way": False,
                "source_block_ids": ["route"],
            }],
        })


def test_topology_agent_retries_once_with_server_validation_feedback() -> None:
    route = ScenarioAuthoringEvidence(
        source_block_id="route",
        document_id="doc",
        title="Route",
        text="The Street leads to the House.",
    )
    contract = ScenarioContract(
        contract_id="retry-topology",
        source_version=1,
        ruleset_id="coc7",
        title="Retry topology",
        initial_scene_id="street",
        locations=(
            LocationSpec(
                location_id="street",
                title="Street",
                initial_visibility="visited",
                source_refs=(route.source_ref(),),
            ),
            LocationSpec(
                location_id="house",
                title="House",
                source_refs=(route.source_ref(),),
            ),
        ),
    )
    candidate = EvidenceBoundContractCandidate(
        contract=contract,
        evidence_blocks=(route.evidence_block(),),
        confidence="medium",
    )
    llm = RetryingTopologyLlm()

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).repair_review_rejection(
            candidate,
            (route,),
            ScenarioContractReview(
                decision="reject",
                review_kind="deterministic_compiler",
                findings=("unreachable_locations: house",),
            ),
        )
    )

    assert repaired is not None
    assert len(repaired.contract.location_links) == 1
    assert len(llm.prompts) == 2
    assert "上次响应被服务器拒绝" in llm.prompts[1]
    assert "invented-route" in llm.prompts[1]


def test_topology_agent_retries_once_after_schema_failure() -> None:
    route = ScenarioAuthoringEvidence(
        source_block_id="route",
        document_id="doc",
        title="Route",
        text="The Street leads to the House.",
    )
    candidate = EvidenceBoundContractCandidate(
        contract=ScenarioContract(
            contract_id="schema-retry-topology",
            source_version=1,
            ruleset_id="coc7",
            title="Schema retry topology",
            initial_scene_id="street",
            locations=(
                LocationSpec(
                    location_id="street",
                    title="Street",
                    initial_visibility="visited",
                    source_refs=(route.source_ref(),),
                ),
                LocationSpec(
                    location_id="house",
                    title="House",
                    source_refs=(route.source_ref(),),
                ),
            ),
        ),
        evidence_blocks=(route.evidence_block(),),
        confidence="medium",
    )
    llm = SchemaRetryingTopologyLlm()

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).repair_review_rejection(
            candidate,
            (route,),
            ScenarioContractReview(
                decision="reject",
                review_kind="deterministic_compiler",
                findings=("unreachable_locations: house",),
            ),
        )
    )

    assert repaired is not None
    assert len(repaired.contract.location_links) == 1
    assert len(llm.prompts) == 2
    assert "wrong_initial_source_field" in llm.prompts[1]


def test_compiler_rejection_runs_bounded_topology_agent_before_record_repair() -> None:
    opening = ScenarioAuthoringEvidence(
        source_block_id="opening",
        document_id="doc",
        title="Opening",
        text="The investigators begin on the street.",
    )
    route = ScenarioAuthoringEvidence(
        source_block_id="route",
        document_id="doc",
        title="Route",
        text="The street leads to the house.",
    )
    contract = ScenarioContract(
        contract_id="haunting",
        source_version=1,
        ruleset_id="coc7",
        title="The Haunting",
        locations=(
            LocationSpec(
                location_id="street", title="Street", source_refs=(opening.source_ref(),)
            ),
            LocationSpec(
                location_id="house", title="House", source_refs=(route.source_ref(),)
            ),
        ),
    )
    candidate = EvidenceBoundContractCandidate(
        contract=contract,
        evidence_blocks=(opening.evidence_block(), route.evidence_block()),
        confidence="medium",
    )
    llm = TopologyLlm({
        "initial_scene_slot": 0,
        "initial_scene_source_block_ids": ["opening"],
        "links": [{
            "from_slot": 0,
            "to_slot": 1,
            "one_way": False,
            "source_block_ids": ["route"],
        }],
    })
    review = ScenarioContractReview(
        decision="reject",
        review_kind="deterministic_compiler",
        findings=(
            "authored_initial_scene_missing; unreachable_locations: house",
        ),
    )

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).repair_review_rejection(
            candidate, (opening, route), review
        )
    )

    assert repaired is not None
    assert repaired.contract.initial_scene_id == "street"
    assert repaired.contract.location_links[0].source_refs == (route.source_ref(),)
    assert repaired.contract.locations[1].initial_visibility == "hidden"
    assert repaired.assumptions[0] == (
        "Source-asserted initial scene selected: Street [opening]"
    )
    assert llm.calls == 2


def test_topology_agent_includes_structured_opening_blocks_without_title_pairs() -> None:
    room_source = ScenarioAuthoringEvidence(
        source_block_id="room-source",
        document_id="doc",
        title="Gardiner details",
        text="The room contains a bookcase and a body.",
        page=10,
    )
    heading = ScenarioAuthoringEvidence(
        source_block_id="opening-heading",
        document_id="doc",
        title="Opening Scene",
        text="Opening Scene",
        page=9,
        section_path=("Introduction", "Opening Scene"),
    )
    body = ScenarioAuthoringEvidence(
        source_block_id="opening-body",
        document_id="doc",
        title="Opening Scene",
        text="You have all gathered outside Gardiner's room.",
        page=9,
        section_path=("Introduction", "Opening Scene"),
    )
    contract = ScenarioContract(
        contract_id="structured-agent-opening",
        source_version=1,
        ruleset_id="coc7",
        title="Structured agent opening",
        locations=(
            LocationSpec(
                location_id="room",
                title="Room",
                source_refs=(room_source.source_ref(),),
            ),
        ),
    )
    candidate = EvidenceBoundContractCandidate(
        contract=contract,
        evidence_blocks=tuple(
            item.evidence_block() for item in (room_source, heading, body)
        ),
        confidence="medium",
    )
    llm = TopologyLlm({
        "initial_scene_slot": 0,
        "initial_scene_source_block_ids": ["opening-body"],
        "links": [],
    })

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).repair_review_rejection(
            candidate,
            (room_source, heading, body),
            ScenarioContractReview(
                decision="reject",
                review_kind="deterministic_compiler",
                findings=("authored_initial_scene_missing",),
            ),
        )
    )

    assert repaired is not None
    assert repaired.contract.initial_scene_id == "room"
    assert repaired.assumptions == (
        (
            "Source-asserted initial scene selected: Room "
            "[opening-body]"
        ),
    )
    assert llm.calls == 2


def test_topology_agent_cannot_select_a_slot_outside_proven_entry_options() -> None:
    opening = ScenarioAuthoringEvidence(
        source_block_id="opening",
        document_id="doc",
        title="Opening Scene",
        text="The investigators begin on the Street.",
    )
    contract = ScenarioContract(
        contract_id="bounded-entry-options",
        source_version=1,
        ruleset_id="coc7",
        title="Bounded entry options",
        locations=(
            LocationSpec(
                location_id="street",
                title="Street",
                source_refs=(opening.source_ref(),),
            ),
            LocationSpec(
                location_id="house",
                title="House",
                source_refs=(opening.source_ref(),),
            ),
        ),
    )
    candidate = EvidenceBoundContractCandidate(
        contract=contract,
        evidence_blocks=(opening.evidence_block(),),
        confidence="medium",
    )
    llm = TopologyLlm({
        "initial_scene_slot": 1,
        "initial_scene_source_block_ids": ["opening"],
        "links": [],
    })

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).repair_review_rejection(
            candidate,
            (opening,),
            ScenarioContractReview(
                decision="reject",
                review_kind="deterministic_compiler",
                findings=("authored_initial_scene_missing",),
            ),
        )
    )

    assert repaired is None
    assert llm.calls == 2


def test_topology_agent_batches_large_evidence_and_merges_verified_selections() -> None:
    opening = ScenarioAuthoringEvidence(
        source_block_id="opening",
        document_id="doc",
        title="Opening",
        text="The investigators begin on the Street.",
    )
    filler = tuple(
        ScenarioAuthoringEvidence(
            source_block_id=f"filler-{index}",
            document_id="doc",
            title="Background",
            text=("Street and House are discussed without a route. " + "x" * 5_000),
        )
        for index in range(8)
    )
    route = ScenarioAuthoringEvidence(
        source_block_id="route-last",
        document_id="doc",
        title="Route",
        text="The Street leads to the House.",
    )
    contract = ScenarioContract(
        contract_id="batched-topology",
        source_version=1,
        ruleset_id="coc7",
        title="Batched topology",
        locations=(
            LocationSpec(
                location_id="street", title="Street", source_refs=(opening.source_ref(),)
            ),
            LocationSpec(
                location_id="house", title="House", source_refs=(route.source_ref(),)
            ),
        ),
    )
    candidate = EvidenceBoundContractCandidate(
        contract=contract,
        evidence_blocks=tuple(
            item.evidence_block() for item in (opening, *filler, route)
        ),
        confidence="medium",
    )
    review = ScenarioContractReview(
        decision="reject",
        review_kind="deterministic_compiler",
        findings=("authored_initial_scene_missing; unreachable_locations: house",),
    )
    llm = BatchedTopologyLlm()

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).repair_review_rejection(
            candidate, (opening, *filler, route), review
        )
    )

    assert repaired is not None
    assert repaired.contract.initial_scene_id == "street"
    assert tuple(
        (item.from_location_id, item.to_location_id)
        for item in repaired.contract.location_links
    ) == (("street", "house"),)
    assert len(llm.user_prompts) >= 2
    assert max(map(len, llm.user_prompts)) < 40_000


def test_repair_contracts_only_proven_reserved_travel_identity_conflict() -> None:
    evidence = ScenarioAuthoringEvidence(
        source_block_id="route",
        document_id="doc",
        text="The Street leads to the House.",
    )
    source = evidence.source_ref()
    link = LocationLink(
        from_location_id="street",
        to_location_id="house",
        source_refs=(source,),
    )
    reserved_id = canonical_travel_operator_id(
        link, origin="street", destination="house"
    )
    contract = ScenarioContract(
        contract_id="reserved-travel-repair",
        source_version=1,
        ruleset_id="coc7",
        title="Reserved travel repair",
        initial_scene_id="street",
        locations=(
            LocationSpec(
                location_id="street",
                title="Street",
                initial_visibility="visited",
                source_refs=(source,),
            ),
            LocationSpec(
                location_id="house", title="House", source_refs=(source,)
            ),
        ),
        location_links=(link,),
        operators=(
            ActionOperator(
                operator_id=reserved_id,
                title="Model-authored conflicting travel",
                policy="automatic",
                source_refs=(source,),
            ),
        ),
    )
    candidate = EvidenceBoundContractCandidate(
        contract=contract,
        evidence_blocks=(evidence.evidence_block(),),
        confidence="medium",
    )
    finding = (
        "canonical_travel_conflict: Published operator conflicts with canonical "
        f"location travel: {reserved_id}"
    )
    review = ScenarioContractReview(
        decision="reject",
        review_kind="deterministic_compiler",
        findings=(finding,),
    )

    contracted, removed = (
        ConstrainedScenarioContractAuthoringAdapter
        .contract_compiler_proven_canonical_travel_conflicts(candidate, review)
    )
    compiled = ScenarioContractCompiler().compile(contracted.contract)

    assert removed == (reserved_id,)
    assert compiled.contract is not None
    canonical = next(
        item for item in compiled.contract.operators if item.operator_id == reserved_id
    )
    assert canonical.title == "Street → House"


def test_independent_review_cannot_trigger_new_topology_authority() -> None:
    evidence = ScenarioAuthoringEvidence(
        source_block_id="opening", document_id="doc", text="Opening text."
    )
    contract = ScenarioContract(
        contract_id="safe",
        source_version=1,
        ruleset_id="coc7",
        title="Safe",
        locations=(
            LocationSpec(
                location_id="room", title="Room", source_refs=(evidence.source_ref(),)
            ),
        ),
    )
    candidate = EvidenceBoundContractCandidate(
        contract=contract,
        evidence_blocks=(evidence.evidence_block(),),
        confidence="medium",
    )
    llm = TopologyLlm({})

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).repair_review_rejection(
            candidate,
            (evidence,),
            ScenarioContractReview(
                decision="reject",
                review_kind="independent_ai",
                findings=("initial_scene_missing",),
            ),
        )
    )

    assert repaired is None
    assert llm.calls == 0


def test_review_repair_contracts_consecutive_canonical_travel_conflicts() -> None:
    evidence = ScenarioAuthoringEvidence(
        source_block_id="route",
        document_id="doc",
        text="The Street leads to the House, which leads to the Cellar.",
    )
    source = evidence.source_ref()
    links = (
        LocationLink(
            from_location_id="street",
            to_location_id="house",
            source_refs=(source,),
        ),
        LocationLink(
            from_location_id="house",
            to_location_id="cellar",
            source_refs=(source,),
        ),
    )
    reserved_ids = tuple(
        canonical_travel_operator_id(
            link,
            origin=link.from_location_id,
            destination=link.to_location_id,
        )
        for link in links
    )
    contract = ScenarioContract(
        contract_id="consecutive-reserved-travel-repair",
        source_version=1,
        ruleset_id="coc7",
        title="Consecutive reserved travel repair",
        initial_scene_id="street",
        locations=tuple(
            LocationSpec(
                location_id=location_id,
                title=title,
                initial_visibility="visited"
                if location_id == "street"
                else "hidden",
                source_refs=(source,),
            )
            for location_id, title in (
                ("street", "Street"),
                ("house", "House"),
                ("cellar", "Cellar"),
            )
        ),
        location_links=links,
        endings=(
            EndingRule(
                ending_id="reach-cellar",
                title="Reach the cellar",
                all_conditions=(
                    StateCondition(path="scene_id", operator="eq", value="cellar"),
                ),
                source_refs=(source,),
            ),
        ),
        operators=tuple(
            ActionOperator(
                operator_id=operator_id,
                title="Model-authored conflicting travel",
                policy="automatic",
                source_refs=(source,),
            )
            for operator_id in reserved_ids
        ),
    )
    candidate = EvidenceBoundContractCandidate(
        contract=contract,
        evidence_blocks=(evidence.evidence_block(),),
        confidence="medium",
    )
    llm = TopologyLlm({})
    adapter = ConstrainedScenarioContractAuthoringAdapter(llm)
    initial = ScenarioContractCompiler().compile(candidate.contract)
    review = adapter.review_compilation_failure(candidate, initial.report)

    assert review is not None
    repaired = asyncio.run(
        adapter.repair_review_rejection(candidate, (evidence,), review)
    )

    assert repaired is not None
    compiled = ScenarioContractCompiler().compile(repaired.contract)
    assert compiled.contract is not None
    assert not {
        item.code for item in compiled.report.issues
    } & {"canonical_travel_conflict"}
    assert set(reserved_ids) <= {
        item.operator_id for item in compiled.contract.operators
    }
    assert llm.calls == 0


def test_topology_repair_fails_closed_for_unsourced_location_catalog() -> None:
    evidence = ScenarioAuthoringEvidence(
        source_block_id="opening", document_id="doc", text="Opening text."
    )
    candidate = EvidenceBoundContractCandidate(
        contract=ScenarioContract(
            contract_id="unsourced",
            source_version=1,
            ruleset_id="coc7",
            title="Unsourced",
            locations=(LocationSpec(location_id="room", title="Room"),),
        ),
        evidence_blocks=(evidence.evidence_block(),),
        confidence="medium",
    )
    llm = TopologyLlm({})

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).repair_review_rejection(
            candidate,
            (evidence,),
            ScenarioContractReview(
                decision="reject",
                review_kind="deterministic_compiler",
                findings=("authored_initial_scene_missing",),
            ),
        )
    )

    assert repaired is None
    assert llm.calls == 0
