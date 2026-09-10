# ScenarioContract design notes

## Scope

`ScenarioContract` must compile the playable semantics of many source layouts. It must
not reproduce the headings or route of one module. A source document remains evidence;
the contract is a separately versioned, reviewable executable projection.

Two retained source modules were compared during the initial design audit:

- a compact timed scenario organized mainly as a sequence of locations, with action
  limits, a pursuing threat, optional routes, situational modifiers, and several endings;
- an investigative scenario organized as an open location network, with core clues,
  handouts, NPC attitudes, repeatable research, room reactions, hazards, combat, and an
  antagonist who escalates in response to investigation.

The comparison rules out a single scene list as the domain model. Both documents also
mix read-aloud prose, private Keeper explanation, rules, examples, optional advice, and
translation/editor commentary. Import must preserve provenance and uncertainty instead
of treating every paragraph as executable fact.

Official Call of Cthulhu material describes play as investigators stating intent while
the Keeper presents the world and applies rules consistently. It also describes clues as
opening further avenues of research or exploration. Chaosium's creator guidance advises
playtesting specifically to find ways players stray from expected narrative and scenes.
Those principles support a graph of opportunities plus explicit state transitions, not
a mandatory ordered script.

Sources:

- [Chaosium rules overview](https://cthulhuwiki.chaosium.com/rules/)
- [Chaosium Miskatonic Repository creator FAQ](https://www.chaosium.com/blogmiskatonic-monday-faq-for-community-creators/)
- [Chaosium short scenario example: What's in the Cellar?](https://www.chaosium.com/content/FreePDFs/CoC/Cult%20of%20Chaos%20Scenarios/What%27s%20in%20the%20Cellar.pdf)
- [Chaosium short scenario example: Dead Boarder](https://www.chaosium.com/content/FreePDFs/CoC/Cult%20of%20Chaos%20Scenarios/Dead%20Boarder.pdf)
- [Chaosium short scenario example: The Lightless Beacon](https://www.chaosium.com/content/FreePDFs/WeAreAllUs/2019/The%20Lightless%20Beacon%20-%20Call%20of%20Cthulhu.pdf)
- [Chaosium Quick-Start scenario: The Haunting](https://www.chaosium.com/content/FreePDFs/CoC/CHA23131%20Call%20of%20Cthulhu%207th%20Edition%20Quick-Start%20Rules.pdf)

The additional official examples broaden the structural corpus without becoming runtime
special cases. One concentrates role-specific motives, clues, a reactive threat, and a
conditional rescue inside a single room. Another uses a deliberately timed sequence of
clue batches and escalation. A third moves from a dangerous arrival into open,
potentially split-party exploration before a scalable assault; failed checks can impose
delay or cost without blocking arrival, environmental evidence can expire, earlier
repairs alter later rescue conditions, and conclusions vary by survival and escape.

Consequently, later schema revisions need composable records for soft and hard clocks,
private actor goals, per-actor or per-group location, expiring evidence, independent
effects from combined checks, fail-forward costs, conditional reinforcement or rescue,
encounter scaling, and outcome-based rewards. These remain generic capabilities selected
from contract state; production code must never infer them from a document title.

## Executable primitives

The first stable schema uses independent, referentially validated records:

- metadata: identity, schema/source/ruleset versions, era, initial state;
- locations and traversable relations, without assuming a linear order;
- actors and entities with initial status, location, visibility, and provenance;
- entity canonical/derived profiles plus bounded mutable runtime state; Agents are created on
  demand from this data rather than persisted per entity;
- clues as facts with discovery opportunities, importance, and reveal effects;
- action operators with preconditions, resolution policy, valid check choices, maximum
  effect, and success/failure commands;
- player-visible `CheckPlan` records containing authorized alternatives, modifier, automatic
  information, ordinary failure stakes and pushed-failure stakes;
- response obligations that require concrete facts, emotion or physical behavior in an NPC reply;
- typed semantic triggers and generic pressure stages whose consequences are contract commands;
- clocks and resources with bounds and explicit advancement causes;
- ending rules evaluated after every committed command batch;
- bounded task methods that decompose a plan into primitive operator IDs;
- reactive NPC/threat policies that select proposals but emit only validated commands;
- narration/source blocks kept outside authoritative mechanics.

Every committed single or parallel action appends a kernel-owned `action_resolved` event containing
its operator and outcome (and its actor for parallel settlement). Even an otherwise no-op action
therefore creates one idempotent, versioned audit event. Read-only condition paths expose the latest
outcome through `events.operator_outcomes.<sha256(operator_id)>.outcome`; the raw operator ID remains
in the event payload. Ending and reaction materializers can select from this closed catalog of
produced paths instead of inventing `facts.*` links.

Coverage materializers keep semantic extraction separate from authority. For a missing clue route,
the model supplies only a source-grounded title; the server owns the discovery operator, hashed fact
path, command, provenance, and clue linkage. For an ending, the model may only select an exact existing
operator outcome. A source-stated ending sentence is evidence for the rule, not evidence that the event
has happened: without a causal operator the blocking coverage obligation remains unresolved. The semantic reviewer receives a server
legend for every hashed outcome path it sees. It checks entailment of materialized records, while the
deterministic coverage compiler—not the reviewer—owns omission detection.

Check/effect supplements make no model call. The ruleset catalogs extract skill keys, difficulty,
effect identifiers, dice payloads, and server titles directly from the cited source. The missing count
comes from the coverage ledger; parallel source checks become player-selectable skill choices unless
the source obligation proves distinct actions. Assembly drops check actions whose cited source cannot
prove their skills and unreferenced automatic actions with no checks or commands. Endings
whose condition paths have no initial-state, world-command, or kernel-outcome producer are discarded
before coverage supplementation; an ending cannot bootstrap its own trigger from its completion commands.
The compiler also rejects commandless ending operators and ungated automatic terminal actions. A genuine
irreversible player decision is represented as an explicit choice; an automatic terminal transition must
be gated by established state. Core clue routes must expose concrete success information, not merely flip
a private fact. A location graph must declare an entry point, which is projected as already visited.

An author may omit primitives that a scenario does not use. A location-only mystery does
not need a clock; a social scenario does not need combat operators. Compiler warnings can
identify missing redundancy or unreachable content without inventing mandatory checks.

Runtime evaluates mandatory triggers and reached pressure stages to a bounded fixed point before
checking endings. Markers are part of the same command batch, so a repeated expansion, request retry,
or later reaction pass cannot apply the same once-per-run consequence twice. Trigger matching uses
typed event and target identifiers plus state conditions; production code never dispatches on prose
keywords, module names, example actions, or NPC names.

## Import layers

The compiler is a staged pipeline rather than one large prompt:

1. Extract source blocks with page/paragraph provenance and visibility.
2. Classify blocks as prose, Keeper guidance, entity, clue, rule, example, option, or
   commentary. Low confidence remains a candidate.
3. Normalize names and resolve references without deleting the original evidence.
   Explicit numbered scene-heading blocks are also materialized as stable server-owned
   location candidates after partition-local action slots have been resolved. A model-proposed
   location survives only when an exact source title/section/scene key or an explicit spatial
   sentence proves its role. Same-named rooms under different source parents remain distinct.
4. Propose contract records using closed schemas and known ruleset identifiers.
5. Run structural validation, reference validation, reachability, ending satisfiability,
   and conflict checks.
6. Human KP reviews in no-model mode. Full AI KP may auto-review only policy-allowed,
   evidence-backed candidates; ambiguous or high-impact candidates remain visible.
7. Publish an immutable contract version. Runtime state refers to that exact version.

Automatic publication adds a stricter evidence gate after structural compilation. Every
executable location, relation, entity, clock, resource, clue, operator, task method,
reactive policy, and ending must cite an extracted block from the exact compilation
corpus. Unknown block IDs and disagreeing locators are errors. In full AI mode, only a
high-confidence candidate with no unresolved assumptions is auto-publishable; balanced
and conservative modes retain the same valid candidate as a reviewable draft.
Partition confidence and assembly assumptions are review inputs, not an irreversible
ceiling. The full-AI reviewer receives the complete bounded assumption set and must
explicitly attest that every item is source-supported or describes output already removed
without leaving a mechanic gap. Only an approval with that attestation produces a derived
high-confidence, assumption-free candidate for the evidence gate. The pre-review candidate,
all assumptions and the review response remain persisted; no human-review mode promotes it.

The gate also maintains a deterministic source-coverage ledger. Conservative rules derive
advisory world-materialization obligations only from substantive prose, so headings and
short labels cannot manufacture executable records. Explicit checks, pressure, ruleset
effects, and terminal conditions are blocking obligations. Coverage is reconciled against
the kinds of records that actually cite each source block, not against model claims. Missing
Only check instructions that locally name their concrete skill or attribute are blocking;
unnamed cross-paragraph lead-ins remain advisory because binding them would require guessing
the missing term. Blocking coverage prevents unattended publication; advisory gaps remain visible without
forcing the review loop to recreate a record it already rejected. This ledger
is intentionally not a proof of semantic completeness: classifier errors and mechanics
outside the closed recognizers still require the independent semantic review and human
inspection.

Source-facing checks use a second deterministic boundary. The model emits an abstract
term such as `灵感` or `修理`; the installed ruleset owns the normalized aliases and
target keys. Exact terms resolve to one characteristic or skill. Broad concepts may
resolve to several allowed choices so the player's declared method remains decisive.
Unknown terms resolve to nothing, downgrade the operator to clarification, and add an
assumption that blocks automatic publication. A broad mapping is an adaptation option,
not evidence that the source explicitly required every candidate skill.

No stage dispatches on a module title. Evaluation fixtures may assert that facts from a
named source compile correctly, but production behavior depends only on schema records.

Deleting an unsupported location is a typed graph contraction, not a string replacement.
Commands and condition-bearing operators, methods, links, obligations, reactive/trigger rules,
signal bands, and endings that require the removed identity are contracted at their smallest safe
container. Arbitrary fact values that happen to equal the location ID remain untouched.

The topology repair Agent may only select opaque existing location slots and exact source IDs from
its current bounded evidence batch. Undirected links are selected once and existing links are not
repeated. One validation failure receives one exact server-error feedback retry; a second failure
returns no mutation and leaves reachability as a release blocker.

Reachability has one definition: the bounded kernel state exploration, including every executable
`set_scene` outcome whose preconditions can actually be satisfied. The compiler projects its
reachable/unreachable lists from that exploration instead of maintaining a link-only graph.

An explicit source instruction to give a numbered handout after a successful check crosses a
server-owned delivery boundary. The server binds the checked operator, verbatim public handout
body, and a stable fact; later scene opportunities may consume that fact. The model never authors
the fact path or substitutes a paraphrase for the player-visible content.

## Implemented lifecycle and current limit

A source document is not assumed to equal one scenario. Deterministic top-level
chapter/adventure boundaries expose stable source ranges; a compendium requires an
explicit KP range selection before authoring, while ordinary scene headings stay inside
one scenario. The selected range supplies the server-owned title and bounded evidence.
Production code never classifies a range from a book title or a scenario-specific name.

The KP director now exposes the contract lifecycle as a product workflow rather than a
repository-only service. KP-only endpoints can generate from module chunks, compile
manual JSON without a model, list immutable versions, publish with optimistic row
versions, and bind one published version to a module run. The director UI exposes the
same sequence and its validation issues. In full-AI mode, a source-complete,
high-confidence, assumption-free result may publish and bind automatically only after a
separate structured model call approves semantic support for every claimed mechanic;
conservative and balanced modes retain a draft. Invalid review output, review findings,
failure to attest every authoring assumption, or a bounded/truncated source window adds an
unresolved assumption and therefore cannot auto-publish.

Model calls occur in a dedicated SQLite-backed background worker, outside write
transactions. A job stores its full-corpus fingerprint and server-owned contract
identity, source version, ruleset, automation level and optional run binding. Each
bounded evidence partition is a separate row with its own state and serialized IR
result. The worker commits after each partition, so startup recovery requeues only the
in-flight partition and preserves all successful work. Claim attempts prevent a stale
worker from completing a recovered job. Transient failures use bounded retry; KP may
manually retry an exhausted but recoverable job.

Immediately before persistence, the worker takes an immediate transaction and
recomputes the full module fingerprint. A changed source is a permanent failure: the
stale snapshot cannot be retried and a new job must be created from current evidence.
Server-owned contract identity, source version, ruleset and title never come from model
output, while every cited source locator must exactly match the supplied evidence
catalog. The KP director polls only job summaries (state, stage, progress, attempts and
error); partition evidence and IR remain private server state.

The authoring adapter now partitions the bounded source window and asks the model for a
compact `ScenarioIrBatch`, never a complete contract. IR parsing is a whitelist
projection: harmless unknown presentation fields are discarded, while missing required
authority fields, unknown source block IDs and invalid closed command kinds still fail.
The deterministic assembler restores full source locators, normalizes bracket/dotted fact
paths, merges identical records, records conflicting definitions as assumptions, infers
clue discovery only from actions that set the same fact, and safely removes or downgrades
unprovable links, targets and check policies. Every such non-equivalent downgrade adds an
assumption and therefore prevents automatic publication.

Consequence signals are read-only projections, not a way to materialize world state. The
assembler computes the paths produced by initial facts, declared entities/resources/clocks,
safe action commands and reactive-policy commands. A signal whose source or band condition
is outside that graph is discarded with an assumption before strict compilation; source
coverage still reports the missing mechanic.

Record-addressable validation failures no longer discard that work. If every error can
be traced to a concrete IR `group/index`, the server creates a closed repair plan with
the original failing records, their validation messages, the relevant record schemas,
and the same bounded evidence catalog. The model must return exactly one replacement for
each authorized target. Missing, duplicate, additional, out-of-range, schema-invalid or
foreign-source replacements are rejected. Valid stable identity fields already present
on the failing record cannot be renamed, and records outside the plan are neither sent
nor mutated. Successful, failed and deterministically discarded repair targets are
persisted with their partition and remain visible in the final KP job diagnostics. If
bounded repair is exhausted, only the server-addressed invalid records may be removed;
the smaller IR must validate again, and source coverage plus independent review still
decide whether it can publish. Inert top-level model commentary outside `replacements`
is ignored, but the replacement target set and every replacement record remain closed
and exact. Final-contract invariants that are locally decidable, such as a non-empty
ending trigger, are duplicated in the IR schema so they enter this repair path before
assembly. Root JSON failures, collection-limit
errors and other non-addressable failures deliberately retain bounded whole-partition
retry because the server cannot prove a narrower mutation boundary.

Source selection is cost-bounded rather than paragraph-count-led. The full authoring
window allows at most 80k characters and 256 source blocks; each model partition allows
at most 28k characters and 16 blocks. This matters for old Word imports, which commonly
produce hundreds of tiny paragraph chunks. The real `常暗之厢` UI import contained 243
non-empty blocks but only about 9.3k characters: the former 32-block cap sent 32/243 and
incorrectly forced `corpus_truncated`; the new bounds keep all 243 in sixteen partitions.

A real UI upgrade exposed a pre-release v60 database whose job tables predated three
columns already present in the checked-in migration definition. Migration v62 treats
that deployed state as authoritative evidence: it idempotently inspects both tables and
adds `retryable`, `model_attempt_count`, and `validation_errors_json` when absent. Fresh
databases are unchanged, while existing campaigns and imported modules remain intact.

Migration v63 adds a separate durable queue for coverage-led supplements. After initial
deterministic assembly, uncovered obligations become server-owned targets with an exact
source block, acceptable record kinds and required additional count. The model receives
only targeted evidence and the closed IR groups that can satisfy the target; it cannot
replace successful partitions. Evidence and targets share an immutable payload hash, and
each supplement persists attempts, validation failures, repair diagnostics and result IR.
Recovery resets only running supplements and preserves successful ones. Recreating the
same plan is idempotent; a changed plan for an existing job fails closed instead of
mutating historical evidence.

Initial supplementation is one pass of at most 64 targets. Independent review may then
remove evidence-unsupported records and reopen obligations. Migration v65 gives those
post-review gaps distinct immutable supplement cycles. A cycle compiles only current gaps,
uses a new globally unique ID namespace, assembles its IR in isolation, and transactionally
appends the strict result to the latest reviewed candidate. It must never rebuild from the
original batches because that would resurrect rejected records. At most three post-review
closure cycles are allowed, keeping cost bounded; remaining gaps leave the result unpublished.
The candidate, review history, completed cycle and pending cycle are checkpointed so recovery
reuses every successful call. The UI includes all supplement partitions and target counts in
the resumable job progress model.
The same progress envelope is retained when a bounded run finishes as a draft and the operator
requests continuation. Legacy draft results infer only the last completed cycle number from their
immutable rows; they never mutate or reinterpret an existing cycle plan.
Full-AI authoring automatically continues review, record repair, and coverage closure after the
initial request. The persisted global ceiling is sixteen independent reviews and twelve post-review
coverage cycles. Startup requeues checkpointed drafts below both budgets; recovery and automatic
retries never increase them. Exhaustion leaves a stable human-reviewable draft rather than creating
an unbounded autonomous loop or requiring repeated KP clicks during normal convergence.
An authoring-authority revision starts a new bounded review epoch and grants at most three
additional hard-coverage cycles beyond the persisted absolute cycle cursor. Startup selects only
the newest resumable draft per module and skips modules with an active job, so migration cannot
violate the one-active-job invariant.

Each weak-model supplement call owns one target source and at most six obligations for that
source. It may receive up to two neighboring blocks on each side within a five-block/14k
character context window, so a heading and its following trigger can be interpreted together
without broadening the target set. The server assigns a per-partition record-ID prefix and
rejects output outside that namespace, so generic IDs such as `action_1` cannot collide with
an original partition and silently disappear during merging. After bounded record repair
discards an invalid target, the degraded batch must pass the effect catalog, namespace, and
every requested coverage target again; an empty or partially repaired batch is a failed
supplement, not progress.

A supplement that exhausts its bounded semantic attempts is persisted as failed while the
worker continues other source-owned targets. Final assembly uses only succeeded supplements
and produces a draft plus complete coverage diagnostics; transient provider/process failures
still use job-level recovery. Effect parameters may declaratively expose an outcome-pair
separator. For a checked action the authoring boundary splits such source notation into
success and failure commands and validates both resulting payloads against the same closed
catalog. CoC `1/1d6`, for example, becomes `loss=1` and `loss=1d6`; the runtime never accepts
the slash form as a dice expression.

Model-facing IR mirrors final record-local invariants. In particular, a response obligation
must require at least one observable fact, state cue, physical behavior, or automatic answer.
Trigger topics and secrecy boundaries alone do not constitute a response, so such records are
repaired or discarded inside their evidence partition instead of failing whole-contract assembly.

Coverage supplement prompts expose a dependency-closed schema for only the record kinds
allowed by their server-owned targets. Before strict IR validation, the authoring transport
may rewrite a small closed map of unambiguous command aliases and remove `complete_run` from
ending commands because the kernel appends it. Conflicting canonical and alias fields still
fail validation. Both succeeded and semantically failed supplements count as processed work;
the latter remain separately visible and never count as source coverage.
An automatic job retry requeues only an interrupted `running` supplement; a bounded semantic
`failed` result remains terminal for that job attempt and cannot make progress move backward.
An explicit operator retry of a terminal job may requeue failed supplements intentionally.
That explicit transition immediately recomputes progress from still-succeeded children; the
intentional reset is visible at retry time instead of appearing later as a backward jump.
When job-level retries are exhausted, any currently running partition or supplement is
atomically closed as failed with the provider error. Never-started queued work remains queued
so a later explicit retry can distinguish it from work that actually failed.

Explicit check and ruleset-effect obligations use an even narrower action-slot tool. The model
returns only a title, a closed effect key, and bounded branch payloads. The server extracts check
candidates and their regular/hard/extreme difficulty directly from source text through the active
ruleset catalog, and owns IDs, provenance, resolution policy, command construction, and outcome-pair
normalization. Nearby clause boundaries isolate difficulty markers between parallel checks. Other
target kinds remain on the dependency-pruned IR transport and can migrate independently.
Catalog extraction requires the term to occur near check/outcome language, with a dedicated SAN
loss-notation exception, so unrelated attributes in an NPC stat block do not become player choices.
If the source has no catalog-resolvable term, the supplement fails before a model call. The model
cannot propose a skill, preventing local success from degrading to an uncovered clarification
during global assembly.
Generic action-slot materialization always creates a table-visible check. Hidden checks require
a separate evidence-validated rule path and are never delegated to a weak-model boolean slot.
For check-only targets, the response schema omits every effect slot. Any model output that still
contains those unrequested fields is deterministically discarded with diagnostics before strict
validation. Ruleset term normalization treats common Chinese check suffixes such as 检定, 判定,
and 鉴定 as equivalent while retaining source-occurrence and catalog-resolution gates.

The generic IR transport also accepts one provable weak-model citation alias: `chunk_<paragraph>`
is rebound only when that paragraph maps to exactly one evidence block in the current partition.
All ambiguous, arbitrary, or cross-partition IDs still enter bounded repair and fail closed if they
cannot be corrected. For checked actions, a catalog-declared outcome-pair effect placed in `always`
is deterministically moved into success and failure branches; unrelated always commands remain and
both resulting payloads must independently pass the closed effect catalog.

An `ending` classifier hint creates no coverage obligation by itself. The source must also contain
an executable condition or trigger phrase (for example, “if ... then enter ending”, “trigger/achieve
ending”, or “ending condition”). Editorial discussion, reward attribution, and bare ending labels
must not pressure a supplement model into inventing state predicates.

Full-AI semantic review is evidence-partitioned. Each bounded call receives one evidence partition,
only contract records citing that partition, and stable indices for non-kernel authoring assumptions.
The server unions directly supported assumption indices and deduplicates findings. Invalid output,
any rejected batch, or any unsupported non-kernel assumption rejects the aggregate review. This
keeps long modules within weak-model context/output budgets without weakening the independent gate.
Out-of-range assumption indices are inert and discarded: they prove nothing, while unsupported
allowed indices still reject at aggregation. This preserves a valid record audit when a weak model
copies an example index without granting that output any authority.

Executable ending supplements use a separate link tool. Deterministic semantic ranking selects at
most twelve existing operators and enumerates only outcomes that their policies can produce. The
model may choose catalog entries under `all_of` or `any_of`; the server owns the ending ID, source
binding, and `events.operator_outcomes.<hash>.outcome` conditions. The tool schema has no arbitrary
path, command, reward, ruleset effect, or new-operator field. If the existing executable catalog
cannot express the source condition, the target fails closed instead of inventing state.

Coverage is semantic rather than a citation counter. An explicit check obligation
requires a check policy with concrete ruleset skill or attribute choices. An explicit
pressure obligation requires a clock, a consequence signal, or an action/reactive policy
that advances a clock. Automatic actions and prose that merely discusses the idea of a
time-limited module do not qualify. This prevents weak-model output from satisfying a
gate with a correct source ID but an inert record kind.

Character rules state remains outside the generic scenario snapshot. A contract may
request `apply_ruleset_effect`, but its `event_type` and payload must validate against
the installed ruleset's closed effect catalog. Unknown effects, extra parameters and
unbounded dice syntax are compile errors and are also rejected during partition
authoring. The pure kernel records `ruleset_effect_requested` in the versioned scenario
batch; the application consumes the persisted command in the same database transaction
and appends the ruleset's own character event. The idempotency key derives from scenario
batch ID and command index, so replay cannot apply the effect twice. Failure rolls back
both aggregates instead of leaving scenario and character state split. Contracts cannot
name the affected investigator: preview binds each operator effect to the authenticated
action actor. Parallel previews retain that binding per originating action, while
reactive and ending records are rejected from applying actor effects without an action.

The first CoC7 effect is `san_loss(loss)`. Its amount is a constant or bounded dice
expression selected by the already verified SAN-check outcome branch. The CoC7 state
machine records the loss dice and any required INT/bout follow-up dice, updates live SAN
and insanity conditions, and never rolls the SAN check a second time. Source blocks that
explicitly state SAN loss create a separate ruleset-effect coverage obligation; a check
operator alone therefore cannot make such a block look complete.

This implements the durable map/merge execution pipeline. A real
`gpt-oss-20b` stress fixture on 2026-08-11 moved from repeated full-contract rejection to
producing strict candidates. A real UI run imported the original Word document, resumed
three successful partitions after a failed job, applied ten record repairs, safely
discarded three irreparable records, assembled an 18-operator/5-ending contract and passed
independent semantic review. It stayed draft because the old evidence selector had only
sent 32/243 blocks and covered 23/31 obligations; this directly motivated the cost-bounded
window above. A subsequent 243-block/16-partition run completed on its first job attempt
and recovered coverage to 23/43, but independent review rejected missing location links,
ending triggers, clock linkage and explicit checks. The result was not published. This
motivated the coverage-led targeted supplementation now implemented rather than another
blind whole-corpus retry. End-to-end player conclusion remains acceptance work, so generic
long-module, small-model, no-KP play is not yet declared complete.

The remaining acceptance work is:

1. run the persisted supplementation phase against the real long-module UI job and verify
   automatic review, publication and binding without manual gate bypass;
2. expand conservative coverage recognizers with measured false-positive/false-negative
   fixtures rather than treating the initial Chinese patterns as semantic proof;
3. exercise the exact background-generated and published version through long-module UI
   replay, including model outage and process-restart fault injection;
4. compare small- and large-model coverage across multiple generic module structures and
   free-plan playthroughs before declaring unattended compilation accepted.

This keeps context and retry cost proportional to one source partition or rejected
record and gives small and large models the same compiler boundary. Prompt enlargement
or unlimited whole-contract retry is not an acceptable substitute.

## Public-module corpus audit

The four official public scenarios above were rechecked as a structural corpus on
2026-08-11. Their prose uses a small number of recurring semantic forms, but the forms
are combined differently enough that a scene script is not a safe executable model:

- ordered sections and minute marks are commonly pacing advice, while player-selected
  locations, split parties and conditional help remain legal;
- some information is obvious or granted after access has already been earned, while
  other information is obscure and may legitimately remain undiscovered;
- one goal may accept several approaches and therefore several skills; the declared
  roleplay determines which skill is appropriate, not a keyword in the action text;
- regular, hard, extreme and critical results may reveal different things or change the
  magnitude of a consequence, so a Boolean success flag is not always expressive enough;
- repeated research can be legal while consuming time, and a pushed attempt can reveal
  the desired information while also applying an injury, alarm or other failure cost;
- escalation may depend on elapsed time, discovered-clue count, entering a location,
  disturbing an entity, or earlier repairs and requests for help;
- investigator motives and private background facts can change available hooks without
  making one pre-generated character or one route mandatory;
- conclusions are predicates over survival, escape, threat state, discoveries and prior
  choices, often followed by state-dependent rewards or epilogues.

These observations translate into compiler rules rather than title-specific behavior:

1. A source order is `soft` unless the source supplies a causal or timing constraint.
2. Every discovery route declares whether it is automatic, check-gated, repeatable,
   fail-forward, or genuinely missable.
3. Alternative skills are closed contract choices, and the player can always replace the
   semantic model's initial choice with another listed choice before rolling.
4. Check outcome branches use ruleset-returned outcome identifiers; the model never
   upgrades a Boolean result or invents a stronger effect.
5. A pushed or repeated attempt is a new versioned action whose time and risk commands
   are explicit and replayable.
6. Reactive escalation and endings are evaluated after committed command batches, not
   inferred from narration.
7. Generic consequence signals project committed clocks, resources, facts, entities or
   scene conditions at contract-declared visibility and precision. Runtime code does not
   enumerate scenario-specific pressure themes.
   During IR assembly, a KP-only signal that carries player-facing presentation is
   deterministically narrowed: its executable conditions remain, while public titles,
   descriptions and visible-band flags are removed and the repair is recorded as a
   completed normalization diagnostic, separate from unresolved authoring assumptions.
   This information-reducing transform never widens disclosure, does not incorrectly block
   automatic review, and prevents one weak-model presentation mismatch from invalidating
   unrelated source partitions.
   The same boundary downgrades an incomplete or private-path table projection to KP-only,
   reduces exact display without a source path to staged display, and keeps the first of
   duplicate band IDs. Each transform is diagnostic and information-reducing; the assembler
   never invents public prose, state paths, conditions, or values to satisfy presentation.
8. Operators and bounded task methods may declare multilingual `intent_hints`. A pure,
   state-aware retriever ranks large catalogs before model selection; catalog truncation
   never follows source order and never grants resolution authority.
9. Player-facing prose is compiled separately from effects. `public_setup` stays neutral;
   each `narrative_cue` names an executable outcome key and may name only a declared NPC.
   Dynamic overlays may not make a source/secret entity speak. A model may realize these
   public cues after selection, but the verified kernel/ruleset outcome selects the line.

The current schema already covers soft/hard clocks, alternative skill choices, explicit
failure commands, bounded retries through repeated actions, reactive policies and
multiple endings. Before declaring the generic compiler complete, executable outcome
branches, player-facing clue disclosures/handout references, and private role hooks must
also be represented and verified across this corpus.

## Freedom boundary

Source routes are defaults, not exclusive solutions. The runtime first tries contract
operators and bounded compositions. If a player's method is plausible but absent, the AI
or human KP may propose a world expansion containing evidence, assumptions, costs, and
new operators. Validation rejects contradictions, era violations, impossible resources,
unbounded effects, and changes to immutable source facts.

For example, earning money and hiring another person to investigate is not a special-case
route. It is a composition of generic economy, social agreement, delegation, time, NPC
capability, risk, and information-transfer operators. Whether it works depends on the
current world and contract, not on matching the words “gamble” or “hire.”

## Compiler and runtime acceptance

A generic implementation is acceptable only when:

- two structurally different source modules compile without title-specific code;
- important source facts retain page/paragraph provenance;
- the validator finds dangling references, impossible conditions, unreachable mandatory
  anchors, conflicting effects, and unsatisfiable endings;
- a source's expected routes remain playable while causally valid alternatives work;
- required clues cannot disappear solely because of one failed roll unless the contract
  explicitly defines another recovery route;
- identical snapshot, intent, operator, and contract version yield the same preview hash;
- small-model and large-model adapters can only select the same validated candidate IDs;
- completion is derived from ending rules and prevents later actions or stale jobs.

## Model profiles

Full-AI publication uses a bounded review-repair-review protocol. The independent
reviewer returns both human-readable findings and record-addressed issues. A server may
correct a wrong group label only when the supplied record ID exists exactly once across
all replaceable contract collections; it may also recover an issue when a finding
literally contains that unique ID, or an exact title of at least eight characters that is
unique across the whole contract. It never fuzzy-matches titles or invents a target.
Up to three repair rounds may replace or remove exactly the records named in that round,
with unchanged IDs and no new source references. Invalid review transport receives at most
two corrective retries. Every repaired contract is parsed through the strict model and independently
reviewed again; a fourth rejection remains a draft. The review history is an audit artifact, not model
authority. Duplicate issues for one record collapse to one target, and repair transport
is partitioned into one unique record per call before one final whole-contract
validation.
Each replacement is transactionally validated against a temporary whole contract. If it
breaks cross-record references, conservative removal is tried; if removal is also invalid,
the original record is retained while independent targets continue.
Rejected endings are never freely rewritten by the model: they are removed transactionally,
then deterministic source coverage may rematerialize only a source-supported ending.
Reviewer claims that a typed operator lacks a skill it already contains, or that an opaque
server-owned clue-state path/value lacks literal prose support, are deterministically
discarded. This exception does not cover titles, causal effects, provenance or endings.
When a weak reviewer puts an exact record ID only in free-text findings, the server first
recovers the unique structured record anchor and then applies the same authority test; free text
cannot bypass a contradiction already settled by server-validated fields.
Conversely, an explicit `approve` decision is not overturned by an unanchored prose note.
Only a structured issue or a deterministically recovered unique record anchor has blocking
authority; this prevents free text from bypassing the record-addressed review boundary.
An issue's field claim must also belong to its addressed record type. For example, skill-key
claims are valid only for operators and cannot gain authority by targeting a location, clue,
entity, or ending. Reject findings are derived from the surviving addressed issues, so alternate
free-text wording cannot resurrect a server-disproved claim.
After two invalid repair envelopes for one named record, the server may try transactional
removal of only that record. Removal that breaks references is ignored, and coverage plus
independent re-review remain mandatory.
The latest whole-contract-valid repaired candidate is persisted as a job checkpoint.
Review resumption and process recovery continue from it instead of reassembling the
original IR and resurrecting previously removed records.
Operator removal contracts direct typed dependents: endings that test its outcome, core
clues that lose their only discovery route, and task methods that contain the step. The
whole contract and source coverage are recompiled. An automatic operator still produces
the kernel success outcome without requiring a success command.
If this contraction reopens blocking coverage, the next iteration runs a new persisted
supplement cycle before semantic review. Advisory materialization is attempted during initial
assembly but is not regenerated after rejection merely to reach a display metric. The isolated IR assembler accepts outcome paths from the base
contract as read-only external producers (needed for ending rules), then strict validation
checks the combined candidate. Only an approved candidate with complete deterministic hard
coverage can auto-publish; advisory coverage remains part of the audit report.

Persisted mechanical obligations are re-derived from immutable source text during
assembly for `explicit_checks` and `ending_rule`. This lets recovered jobs adopt newer
deterministic inference without regenerating their model partitions. Alternative skills
joined as `A or B` count as one selectable check action, while a failure/success-followup
check remains a separate sequential obligation. A source-explicit conditional ending
clause is materialized server-side: exact operator-title matches become kernel outcome
conditions, otherwise the clause becomes a no-effect observable trigger. The model never
owns IDs, state paths, commands, rewards, damage, or SAN in this fallback.

Both model profiles receive a closed candidate catalog, never raw mutation tools. A small
model sees a compact set of primitive operator IDs and their allowed skill IDs. A large
model may additionally select validated bounded task-method IDs. Invalid JSON, a catalog-
external ID, or an unauthorized skill receives one corrective retry; a second failure
becomes a clarification request with no authority. Every successful selection returns
the complete allowed skill list and remains manually changeable before confirmation.
