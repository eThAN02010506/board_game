import { Brain, Check, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import type {
  CampaignInvestigator,
  ContextAssembly,
  NpcReappearanceCandidate,
  SavedMap,
  TurnProposal,
  WorldExpansionEncounterInput
} from "../../api/types";
import { ProposalEffectList } from "../../shared/ProposalEffectList";
import { statusLabel } from "../../ui/statusLabels";
import { DynamicBranchPlanPreview } from "./DynamicBranchPlanPreview";
import { WorldExpansionContactForm } from "./WorldExpansionContactForm";

type Props = {
  proposals: TurnProposal[];
  activeProposal: TurnProposal | null;
  proposalContext: ContextAssembly | null;
  overrideText: string;
  loading: boolean;
  activeMap: SavedMap | null;
  campaignTime: string;
  contactInvestigators?: CampaignInvestigator[];
  npcReappearanceCandidates?: NpcReappearanceCandidate[];
  onOverrideTextChange: (value: string) => void;
  onSelectProposal: (proposalId: string) => void;
  onRefresh: () => void;
  onInspectContext: () => void;
  onApprove: () => void;
  onReject: () => void;
  onConfirmWorldExpansion: (input: WorldExpansionEncounterInput) => void;
};

export function ProposalPanel(props: Props) {
  const { t } = useTranslation();
  return (
    <section className="tool-panel proposal-panel">
      <div className="panel-heading">
        <h2>{t("proposals.title")}</h2>
        <Check size={18} />
      </div>
      <button className="ghost-button" onClick={props.onRefresh} type="button">
        <RefreshCw size={16} />
        {t("proposals.refreshDrafts")}
      </button>
      <div className="list-stack">
        {props.proposals.map((proposal) => (
          <button
            className={`record-button ${proposal.id === props.activeProposal?.id ? "selected" : ""}`}
            key={proposal.id}
            onClick={() => props.onSelectProposal(proposal.id)}
            type="button"
          >
            <span>
              {statusLabel(proposal.status)}
              {proposal.proposal_kind === "check_consequence" ? t("proposals.checkConsequenceSuffix") : ""}
              {proposal.proposal_kind === "world_expansion" ? t("proposals.worldExpansionSuffix") : ""}
            </span>
            <small>{proposal.player_action}</small>
          </button>
        ))}
      </div>
      <div className="proposal-card">
        {props.activeProposal ? (
          <>
            <span className={`proposal-status ${props.activeProposal.status}`}>
              {statusLabel(props.activeProposal.status)}
              {props.activeProposal.proposal_kind === "check_consequence"
                ? t("proposals.checkConsequenceSuffix")
                : props.activeProposal.proposal_kind === "world_expansion"
                ? t("proposals.worldExpansionSuffix")
                : ""}
            </span>
            <p>{props.activeProposal.public_narration}</p>
            <small>{props.activeProposal.kp_notes}</small>
            {props.activeProposal.action_ruling && (
              <section className={`action-ruling ruling-${props.activeProposal.action_ruling.feasibility}`}>
                <header>
                  <strong>{t("proposals.actionRuling")}</strong>
                  <span>
                    {props.activeProposal.action_ruling.feasibility === "possible"
                      ? t("proposals.feasiblePossible")
                      : props.activeProposal.action_ruling.feasibility === "partial"
                      ? t("proposals.feasiblePartial")
                      : t("proposals.feasibleImpossible")}
                    {" · "}
                    {props.activeProposal.action_ruling.resolution === "automatic"
                      ? t("proposals.resolutionAutomatic")
                      : props.activeProposal.action_ruling.resolution === "check"
                      ? t("proposals.resolutionCheck")
                      : props.activeProposal.action_ruling.resolution === "opposed"
                      ? t("proposals.resolutionOpposed")
                      : t("proposals.resolutionNoRoll")}
                  </span>
                </header>
                <dl>
                  <div><dt>{t("proposals.goal")}</dt><dd>{props.activeProposal.action_ruling.goal}</dd></div>
                  <div><dt>{t("proposals.method")}</dt><dd>{props.activeProposal.action_ruling.method}</dd></div>
                  <div><dt>{t("proposals.target")}</dt><dd>{props.activeProposal.action_ruling.target}</dd></div>
                  <div><dt>{t("proposals.reason")}</dt><dd>{props.activeProposal.action_ruling.reason}</dd></div>
                  <div><dt>{t("proposals.maximumEffect")}</dt><dd>{props.activeProposal.action_ruling.maximum_effect}</dd></div>
                </dl>
                {props.activeProposal.action_ruling.alternative && (
                  <p>{t("proposals.alternativePrefix")}：{props.activeProposal.action_ruling.alternative}</p>
                )}
              </section>
            )}
            {props.activeProposal.world_expansion && (
              <section className="proposal-world-expansion">
                <header>
                  <strong>{props.activeProposal.world_expansion.candidate.subject}</strong>
                  <span>
                    {props.activeProposal.world_expansion.candidate.expansion_kind}
                    {" · "}
                    {props.activeProposal.world_expansion.candidate.confidence}
                  </span>
                </header>
                <p>{props.activeProposal.world_expansion.candidate.proposal}</p>
                <small>{props.activeProposal.world_expansion.candidate.rationale}</small>
                {props.activeProposal.world_expansion.candidate.branch_plan && (
                  <DynamicBranchPlanPreview
                    plan={props.activeProposal.world_expansion.candidate.branch_plan}
                  />
                )}
                {!!props.activeProposal.world_expansion.candidate.assumptions.length && (
                  <details>
                    <summary>{t("proposals.assumptions")}</summary>
                    <ul>
                      {props.activeProposal.world_expansion.candidate.assumptions.map(
                        (item) => <li key={item}>{item}</li>
                      )}
                    </ul>
                  </details>
                )}
                {!!props.activeProposal.world_expansion.candidate.conflicts.length && (
                  <details open>
                    <summary>{t("proposals.conflicts")}</summary>
                    <ul>
                      {props.activeProposal.world_expansion.candidate.conflicts.map(
                        (item) => <li key={item}>{item}</li>
                      )}
                    </ul>
                  </details>
                )}
                <details>
                  <summary>
                    {t("proposals.alternativesCount", { count: props.activeProposal.world_expansion.candidate.alternatives.length })}
                  </summary>
                  {props.activeProposal.world_expansion.candidate.alternatives.map(
                    (alternative) => (
                      <article key={alternative.title}>
                        <strong>{alternative.title}</strong>
                        <p>{alternative.description}</p>
                        <small>{alternative.tradeoff}</small>
                      </article>
                    )
                  )}
                </details>
                <small>
                  {t("proposals.sourceHint", { version: props.activeProposal.world_expansion.module_run_version })}
                </small>
              </section>
            )}
            <div className="effect-list">
              <ProposalEffectList title={t("proposals.effectChecks")} items={props.activeProposal.proposed_checks} />
              <ProposalEffectList title={t("proposals.effectEvents")} items={props.activeProposal.proposed_events} />
              <ProposalEffectList title={t("proposals.effectMemories")} items={props.activeProposal.proposed_memories} />
              <ProposalEffectList title={t("proposals.effectNpcUpdates")} items={props.activeProposal.proposed_npc_updates} />
              <ProposalEffectList title={t("proposals.effectMapMoves")} items={props.activeProposal.proposed_map_moves} />
              <ProposalEffectList title={t("proposals.effectFacts")} items={props.activeProposal.proposed_facts ?? []} />
            </div>
          </>
        ) : (
          <p>{t("proposals.noDrafts")}</p>
        )}
      </div>
      <label>
        {t("proposals.overrideText")}
        <textarea
          value={props.overrideText}
          onChange={(event) => props.onOverrideTextChange(event.target.value)}
        />
      </label>
      <div className="approval-actions">
        <button className="ghost-button" onClick={props.onInspectContext} type="button">
          <Brain size={16} />
          {t("proposals.inspectContext")}
        </button>
        <button
          className="primary-button"
          disabled={props.loading || props.activeProposal?.status !== "draft"}
          onClick={props.onApprove}
          type="button"
        >
          <Check size={16} />
          {t("proposals.approve")}
        </button>
        <button
          className="secondary-button"
          disabled={props.loading || props.activeProposal?.status !== "draft"}
          onClick={props.onReject}
          type="button"
        >
          {t("proposals.reject")}
        </button>
      </div>
      {props.activeProposal?.proposal_kind === "world_expansion" && (
        <WorldExpansionContactForm
          activeMap={props.activeMap}
          campaignTime={props.campaignTime}
          contactInvestigators={props.contactInvestigators ?? []}
          loading={props.loading}
          npcReappearanceCandidates={props.npcReappearanceCandidates ?? []}
          proposal={props.activeProposal}
          onConfirm={props.onConfirmWorldExpansion}
        />
      )}
      {props.proposalContext &&
        props.proposalContext.proposal_id === props.activeProposal?.id && (
          <div className="context-summary">
            <strong>{t("proposals.contextSnapshot")}</strong>
            <small>
              {t("proposals.contextMeta", {
                tokens: props.proposalContext.token_estimate,
                included: props.proposalContext.included_sources.length,
                excluded: props.proposalContext.excluded_sources.length
              })}
            </small>
            <details>
              <summary>{t("proposals.finalPrompt")}</summary>
              <pre>{JSON.stringify(props.proposalContext.final_prompt, null, 2)}</pre>
            </details>
            <details open>
              <summary>{t("proposals.includedSources")}</summary>
              <ul>
                {props.proposalContext.included_sources.map((source) => (
                  <li key={`included-${source.id}`}>
                    <strong>{source.kind}</strong> · {source.label} · {source.visibility}
                    <p>{source.content}</p>
                  </li>
                ))}
              </ul>
            </details>
            <details>
              <summary>{t("proposals.excludedSources")}</summary>
              <ul>
                {props.proposalContext.excluded_sources.map((source) => (
                  <li key={`excluded-${source.id}-${source.excluded_reason}`}>
                    {source.kind} · {source.label} · {source.excluded_reason ?? "unknown"}
                  </li>
                ))}
              </ul>
            </details>
          </div>
        )}
    </section>
  );
}
