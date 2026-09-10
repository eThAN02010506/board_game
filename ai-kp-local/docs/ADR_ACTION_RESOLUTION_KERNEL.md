# ADR: Deterministic action-resolution architecture

Status: accepted, incremental migration in progress

## Context

The existing play loop asks a language model to propose checks and world effects, then
repairs selected cases with text heuristics. This makes ordinary play depend on model
quality and makes the same action capable of producing different authoritative results.
It also mixes four concerns: understanding player language, deciding what is possible,
changing the world, and narrating the outcome.

AI KP Local must support three operating conditions with the same game rules:

- no model, with a human KP selecting or authoring bounded actions;
- a small local model acting as KP through constrained candidates and tools;
- a larger model providing stronger planning, world expansion, NPC portrayal, and prose.

The scenario is a contract, not a railroad. Its original route and checks are defaults.
Alternative approaches are legal when their prerequisites and causal effects are valid,
but an alternative must not silently invent a required gate or bypass a terminal rule.

## Decision

Before action resolution, a ruleset-neutral tabletop protocol classifies the player's
contribution. OOC remarks, visible-world questions and ordinary NPC dialogue remain in the
conversation; only a declared fictional action or time-bearing plan enters operator retrieval.
An asserted result without a method and any ambiguity that changes authority, risk or effect
returns a concrete clarification. The model proposes the frame, but a deterministic policy
validates entity identifiers and selects the route. Failed interpretation falls back to the
closed mechanical boundary and never grants state authority.

The adapter deterministically recognizes only narrow, high-precision forms whose misrouting is
costly for weak models: visible-world perception questions, already-completed result assertions,
and explicit first-person social influence. If two model frames are invalid, an ordered-plan
fallback applies only to explicit first-person “first … then …” syntax. These guards are routing
and recovery policy, not a second action engine; they cannot select an operator or result.

`ActionResolutionKernel` is the sole authority for action feasibility, checks, costs,
effects, clocks, and terminal state. It is deterministic and has no database or model
dependency. Given the same scenario version, snapshot, intent, and operator, it produces
the same `ResolutionPreview` and hash.

Primitive actions are executable `ActionOperator` records with fixed preconditions,
resolution policies, check choices, and command effects. A `ScenarioContract` will bind
operators to anchors, clocks, NPC policies, and endings. Free multi-step actions are
decomposed into bounded HTN-style plans whose primitive steps use the same operators.
NPCs and threats use restricted behavior trees: a deterministic priority selector over
condition/action sequences. Their actions are still preflighted `WorldCommand` batches;
they do not write state directly.

All authoritative mutations are closed-set `WorldCommand` values. A command batch is
preflighted against a copy of the snapshot and later committed atomically with run/action
versions, preview hash, and an idempotency key. Unknown conditions and commands fail
closed. Completing a run freezes further actions and invalidates stale background work.

AI is a semantic and presentation layer. It may map natural language to candidate IDs,
ask a concrete clarification, propose a bounded plan or world expansion, roleplay an NPC,
and narrate a committed result. It cannot choose arbitrary skill identifiers, execute
code, mutate storage, set dice results, or declare an ending. Human KP and player UI use
the same preview/commit boundary. In full AI KP mode, allowed materialization candidates
may be auto-reviewed, while the player still sees and may change the selected reasonable
check before committing it.

Primitive preparation is also source-neutral. AI semantic selection, explicit published
selection, and human-KP catalog selection converge on `prepare_selected_kernel_action`,
which revalidates the operator and requested skill against the bound contract and creates
the deterministic preview and fallback narrative. The first and resumed primitives of a
durable task method, parallel planning, skill re-preview after player choice or restart,
and the final authority recheck before parallel persistence use the same function. The
function lives in `platform/resolution/selected_action.py`: application services and the
transactional parallel-skill rebinder both depend inward on that pure platform boundary.
The rebinder still recomputes from the current persisted contract and snapshot inside its
transaction, but it no longer reconstructs `ActionIntent`, preview, and narrative independently.
The preparation function accepts the frozen `SelectedOperator` DTO only: an operator ID and
an optional requested skill. Model-only route and confidence metadata are discarded by an
explicit semantic adapter before mechanics, while human selections and durable revalidation
construct the DTO directly. This keeps future ruleset plugins independent of any one model
selection protocol.
Source-specific code may choose a candidate or replace presentation prose, but it cannot
construct a separate preview path.

Scenario authority assembly is source-neutral as well. `ScenarioAuthorityContext` freezes the
run identity/version, control mode, spoiler scope, contract version/hash, state version,
persistence status, effective contract and snapshot. AI single-action preparation, parallel
planning/settlement, human manual selection and read-only Need Help load this same DTO. Callers
explicitly choose `require`, `initialize`, or `ephemeral` state policy, so read-only help cannot
initialize game state accidentally. A bound asynchronous result must revalidate this identity
before it can be persisted or returned.

Candidate evidence is projected once as `ScenarioActionCatalog`. The pure projector applies the
shared deterministic ranker, asks the kernel for current availability, and binds each candidate to
its operator, clue, response-obligation, task-method and current-location source references. The AI
selector, human catalog and Need Help brief adapt this frozen value instead of rebuilding parallel
catalogs. Retrieved module or rulebook prose remains supplemental `source_context_only` evidence;
it can improve an answer but cannot add a candidate, skill, command or state authority.

The legacy compatibility shadow also uses this boundary. It wraps the old ruling in an
explicitly synthetic `legacy-shadow` contract and records only a `legacy_projection`
preview for comparison. That contract is never bound to a run and the shadow has no commit
path; sharing preview construction therefore removes drift without granting legacy output
new authority.

Resolved check chains also converge before commit. Single-action Auto KP, persisted
parallel workflows, and restart authority checks call the same `exact_kernel_outcome`
pure function to select only a preview-declared outcome branch. HTTP or worker services
must not wrap or reinterpret that result. Single actions then use
`ResolutionTransactionService`; parallel play uses its dedicated combined-batch transaction
so every player's commands are preflighted and committed atomically as one round.

Every new single-action proposal persists a frozen `KernelAuthorityBasis` in its
`kernel-resolution.v3` payload: run ID, contract-version ID, effective contract hash,
scenario-state version, and preview hash. Parallel settlement captures the same DTO with its
settlement hash and persists it in the command-batch receipt. A shared validator compares the
tuple with the current binding and snapshot inside the write transaction. Historical v2 action
proposals without that basis remain readable but are not executable; they must be recomputed.
This fail-closed boundary prevents a persisted or tampered artifact from acquiring authority
from a newer contract or state. Parallel rounds retain their durable batch fence and combined
transaction rather than being split into single-action commits.

Sequential and parallel commits also call the same pure `resolved_action_commands` projection.
It verifies the exact outcome before returning one stable `action_resolved` event followed by
the preview-declared commands. The sequential transaction resolves the actor from the linked
authoritative player action and verifies its proposal/campaign binding; the parallel workflow
uses each frozen batch actor. Transaction ownership remains separate because a single approval
and an all-or-nothing simultaneous round have different locking and finalization semantics.

For a bound run, human takeover exposes a deterministic primitive-operator catalog ranked
from the submitted action. The KP selects an operator and optionally one of its declared
skills without invoking a model; the player receives the same adjudication and confirmation
record and the same kernel payload is committed. This deliberately does not flatten a task
method into one check: multi-step methods require a durable stepwise plan instance.

Runtime expansion uses a frozen, generic `ActionIntentPlan`; production feasibility code
must not branch on scenario titles, example actions, or plot vocabulary. The first model
call declares steps, prerequisites and intended closed effects. Deterministic validation
then checks the DAG, state paths, player-grounded selection of existing resources/entities,
prior-step provenance and unsupported capabilities. A separate semantic audit checks
completeness and effect fitness and fails closed on rejection or malformed output. Only then
may an authoring call compile the frozen IDs into additive records. Exact two-way binding
rejects both missing commands and commands absent from the intent plan.

The audits improve weak-model recall but are not authority. Runtime expansions cannot
author high-authority navigation or lifecycle commands such as `set_scene`, `move_actor`,
`complete_run`, deletion, registration, or overlay activation. Those capabilities require
an already compiled scenario contract or an installed ruleset effect. Consequently, even
two mistaken semantic approvals cannot directly rewrite the active scene or terminal state.

The durable plan stores the pinned contract hash, ordered primitive IDs, current step,
per-step action and preview hashes, and terminal status. Planning may simulate success only
to prove that the method is structurally coherent. Runtime always previews each primitive
again from the latest committed snapshot. Only a verified successful outcome advances the
cursor; failure, a changed contract, or a newly blocked precondition terminates the plan
without rolling back already committed fiction. Reopening the database resumes the same
pending step, and unique root/step keys prevent retries from duplicating derived actions.

Before either semantic profile calls a model, a deterministic retriever ranks the contract
catalog from normalized explicit intent hints, Unicode word/Chinese character n-grams and
current precondition availability. Small and large profiles receive bounded catalogs of 12
and 32 candidates respectively. This is retrieval, not authority: unavailable but directly
relevant actions may remain visible for an exact kernel explanation, and only the kernel
decides feasibility, checks, commands or endings. Large catalogs without explicit hints
compile with a quality warning instead of silently depending on source/identifier order.

Presentation uses a separate public `KernelNarrativeBundle`. An operator may declare a
neutral `public_setup` and exact `narrative_cues` for success, failure, or ruleset-returned
outcome keys. A cue can name only a declared NPC speaker. The model receives no commands,
secret snapshot, rationale, or maximum-effect internals: only the player action, public
cues, public speaker titles, and the preview hash. It must echo a server-derived basis hash
and exactly preserve outcome keys and speaker IDs. Unsupported numbers, a success claim in
pending/failure prose, malformed output, or a model outage falls back to deterministic cue
text. Direct success prose is selected only after commit; checked prose is selected only
after the ruleset has returned a verified outcome. Prose never chooses the branch.

Semantic selection, expansion authoring, and narrative realization are methods of the
campaign-scoped Director port rather than calls to its raw LLM client. Every method uses
the same in-flight cancellation registry. Application services authorize before calling
and revalidate the durable AI-control snapshot after return and before any overlay or
proposal write, so a concurrent safety pause or human takeover fails closed.

### Freedom and pressure loop

The input surface is open: a player may declare any in-world goal and method. The result
surface remains closed. Runtime tries an existing primitive operator, then a bounded task
method, then a validated additive world-expansion proposal. An action is never rejected
merely because it is absent from the source's expected route, but it may be impossible in
the current world, require clarification, fail a check, consume resources, or achieve only
a bounded partial effect.

The world advances independently of route compliance. Time-consuming operators carry
an outcome-independent clock or resource cost, and reactive policies turn committed
thresholds into warnings and bounded consequences. A stagnation signal may increase the
priority of eligible pressure events, but “not following the plot” is not itself an
authoritative failure condition. Deadlines, pursuit, weather, worsening injuries, rival
investigators, police attention, expiring evidence and nightmares are all data-driven
pressure patterns selected by contract state.

A large model may author candidates for these patterns and may freely portray their
fiction. It cannot create a terminal rule, weaken source invariants, choose arbitrary
state paths, or apply character damage/SAN/resource changes outside the ruleset boundary.
For example, a nightmare can be triggered deterministically by sleep plus prior exposure;
the model narrates or selects an approved motif, while any SAN check and consequence use
the pinned ruleset and a bounded command batch. Small models use the same catalog and
authority boundary, with less authoring freedom and more clarification fallback.

Runtime expansion overlays are additive and run-scoped. The model returns only an
untrusted body; the server supplies the proposal namespace, effective contract hash and
snapshot version. Activation registers new runtime state and advances the scenario
version in one `world_expansion` command batch. A stale hash/version, reused proposal key,
source mutation, missing cost, forbidden control command or compiler error fails closed.
The server also derives the only allowed check keys from the player's explicit wording
and the pinned ruleset catalog. With no explicit check, an expansion may still be an
automatic action with a declared cost, but the model cannot invent a check. The final
merge is compiled with the pinned ruleset effect catalog and rechecks every selected
skill key before persistence.
Activation makes bounded capabilities available; it does not execute the player's action,
materialize contact facts, or add/change an ending. Player confirmation and the normal
kernel settlement remain mandatory.

## Invariants

1. A preview never mutates persistent state.
2. Only validated commands mutate authoritative world state.
3. Checks distinguish required, optional, conditional, and opposed resolution.
4. A successful check cannot exceed the operator's declared maximum effect.
5. Scenario endings are evaluated after every committed batch.
6. A completed run rejects new actions and stale jobs.
7. Model size changes proposal quality, never authority.
8. Scenario source evidence and executable contracts are separately versioned.
9. No condition, effect, or world-expansion rule executes arbitrary code.
10. Background job lifecycle transitions are validated by one pure state machine; SQL
    persistence supplies atomic compare-and-swap claims, not alternate lifecycle rules.
11. A legal detour never freezes scenario pressure; every declared time/resource cost is
    committed independently of success, and pressure effects are condition-triggered.
12. Stagnation is advisory input to eligible pressure policies, never a hidden railroad
    rule or permission for the model to mutate state.
13. Pending narration cannot claim an outcome; public outcome narration is selected only
    from a kernel/ruleset-verified result key.
14. Model-authored presentation receives public cues, never authoritative commands or
    secret scenario state, and validation failure cannot make the game unplayable.
15. Runtime expansion uses a scenario-neutral intent IR, deterministic prerequisite and
    binding validation, semantic completeness audit, then frozen-plan authoring. Missing inputs
    produce a concrete player question; an unestablished capability fails closed.
16. A ruleset skill key is necessary but not sufficient authority. The declared method
    must fit its domain, and high-risk actions require executable ruleset consequences.
17. Runtime expansion cannot manufacture high-authority scene, actor, lifecycle, deletion,
    registration, or overlay commands; those originate in compiled contracts/plugins.
18. AI KP and human KP never assemble independent run/contract/snapshot authority; bound paths
    share one frozen context and reject identity, version, spoiler-scope or state drift.
19. AI selection, human selection and help advice share candidate IDs, skill allow-lists and
    contract evidence; supplemental retrieved prose never grants executable authority.
20. Sequential and parallel commits persist the same `KernelAuthorityBasis` shape and obtain
    `action_resolved` plus outcome commands from one pure projection; sharing this authority does
    not collapse their distinct transaction scopes.

## Runtime policy details

Task methods contain at most eight primitive steps. Their dependency graph is validated
as a DAG, actor bindings are explicit, and previews simulate an assumed-success path only
to expose feasibility, costs, checks, and terminal effects. Actual checks and commits
remain stepwise and versioned.

Reactive policies execute at most one rule per entity for an event. Policies are ordered
by stable ID; rules use descending priority and stable rule ID. Later policies observe
the preflighted result of earlier policies, and terminal state stops further activation.
This makes simultaneous-looking NPC and hazard reactions reproducible without asking a
model to arbitrate hidden ordering.

Clocks declare whether they are `soft` pacing guidance or `hard` scenario pressure. Both
remain bounded counters; contract conditions and reactive rules define threshold effects
instead of embedding module-specific clock behavior in runtime code.

`ConsequenceSignalSpec` is the generic presentation boundary for committed world state.
It may read a clock, resource, fact, entity or scene condition and deterministically select
one bounded band. Contracts choose table/KP visibility and exact/stage/narrative precision.
The table projection hashes internal identifiers and omits source paths, conditions, real
batch identifiers and global state versions; the KP projection retains diagnostics. Time,
nightmares, pursuit or weather are content examples, never runtime signal types.

The durable Auto KP queue keeps its existing idempotent database claims and player/KP
visibility, but all claim, success, attention, failure, retry, cancellation, and stale-job
recovery transitions use the same state machine. Retry delay is deterministic bounded
exponential backoff, so repeated failures remain observable and cannot spin indefinitely.

Parallel actions are evaluated against one starting snapshot, never against effects from
another player's preview. After required checks have outcomes, commands are ordered by
explicit priority and stable action ID, conflicting exclusive assignments are rejected,
and additive resource costs remain separate. The combined batch is preflighted once, so
shared-resource bounds and ending rules see the whole round. A successful settlement is
stored as one immutable command-batch receipt and advances the scenario version exactly
once; optimistic version checks prevent stale or partial commits.

## Migration

The migration is deliberately incremental:

1. Add pure contracts, reducer, hashing, and characterization tests.
2. Persist kernel previews beside legacy proposals in read-only shadow mode.
3. Compile representative linear, node-based, timed, and location-based scenario fixtures.
4. Add bounded plans, resources, economy, and validated world expansion.
5. Add NPC behavior policies, threats, and recoverable background jobs.
6. Add multiplayer intent collection and one atomic settlement.
7. Add module compilation and constrained small/large-model adapters.
8. Switch the default path, remove text heuristics, and pass real UI/replay E2E against
   multiple untouched source modules. Named modules are acceptance samples only; none may
   introduce title-specific code, fields, prompts, or compiler branches.

During shadow mode, legacy behavior remains the user-visible authority. Differences are
recorded for evaluation; the kernel must not double-apply effects.

Primitive operator selection has completed this migration: AI and human-KP entrypoints now
share the source-neutral preparation boundary. Task-method orchestration and runtime world
expansion remain specialized coordinators, but every primitive step still re-enters the same
kernel preview/commit boundary.

## Consequences and risks

Ruleset-owned character resources are not mirrored into `ScenarioSnapshot.resources`.
Scenario commands use the closed `apply_ruleset_effect` request, validated against the
installed plugin catalog. Kernel commit persists the scenario request and consumes it
through the plugin-backed character state machine under one savepoint. This preserves a
single source of truth, atomic rollback and event replay while keeping CoC7 SAN semantics
out of the generic reducer.

The design adds explicit domain data and compilation work, but reduces prompt size,
model calls, non-reproducible behavior, and repair heuristics. Scenario authors need clear
validation errors when a contract is incomplete. Free plans are intentionally bounded;
unsupported steps become clarification or world-expansion proposals instead of arbitrary
state changes.
