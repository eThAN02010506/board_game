import {
  AlertTriangle,
  BookOpenCheck,
  ClipboardList,
  Dices,
  MessageSquareText,
  ShieldCheck
} from "lucide-react";
import { forwardRef } from "react";

import type { DirectorHelpAdvice } from "../../api/types";

type Props = {
  advice: DirectorHelpAdvice;
  historical?: boolean;
};

const statusLabels: Record<DirectorHelpAdvice["status"], string> = {
  answered: "已有明确建议",
  partial: "部分信息可用",
  clarify: "需要补充问题",
  no_evidence: "缺少可靠依据",
  refused: "无法安全回答"
};

const confidenceLabels: Record<DirectorHelpAdvice["confidence"], string> = {
  high: "高置信度",
  medium: "中等置信度",
  low: "低置信度"
};

const evidenceVisibilityLabels: Record<
  DirectorHelpAdvice["citations"][number]["visibility"],
  string
> = {
  player: "玩家可见",
  table: "全桌可见",
  kp: "仅 KP",
  secret: "秘密资料"
};

type DirectorHelpPolicy = Exclude<NonNullable<DirectorHelpAdvice["action"]>["policy"], null>;

const policyLabels: Record<DirectorHelpPolicy, string> = {
  automatic: "无需检定",
  choice: "由 KP 或玩家选择",
  required_check: "需要检定",
  optional_check: "可选检定",
  conditional_check: "条件检定",
  opposed_check: "对抗检定",
  impossible: "当前无法完成",
  clarification: "先向玩家确认"
};

function list(items: string[]) {
  if (!items.length) return null;
  return <ul>{items.map((item, index) => <li key={`${index}:${item}`}>{item}</li>)}</ul>;
}

function sourceReferenceLabel(
  reference: DirectorHelpAdvice["citations"][number]["source_refs"][number]
) {
  return [
    reference.document_id,
    reference.page === null ? null : `p.${reference.page}`,
    reference.paragraph === null ? null : `段落 ${reference.paragraph}`
  ].filter(Boolean).join(" · ");
}

export const DirectorHelpAdviceCard = forwardRef<HTMLElement, Props>(
  function DirectorHelpAdviceCard({ advice, historical = false }, ref) {
    const selectedSkill = advice.action?.skill_choices.find(
      (choice) => choice.skill_key === advice.action?.selected_skill_key
    );

    return (
      <article
        aria-label={historical ? "历史 AI 带团建议" : "AI 带团建议"}
        className={`director-help-result confidence-${advice.confidence}`}
        ref={ref}
        tabIndex={historical ? undefined : -1}
      >
        <header>
          <div>
            <span className={`director-help-status status-${advice.status}`}>
              {statusLabels[advice.status]}
            </span>
            <span className={`director-help-confidence ${advice.confidence}`}>
              {confidenceLabels[advice.confidence]}
            </span>
          </div>
          <small>
            {historical
              ? "历史快照 · 基于当时状态 · 不可执行"
              : "只读建议 · 未改变游戏状态"}
          </small>
        </header>

        <section className="director-help-answer">
          <h3><ShieldCheck aria-hidden="true" size={18} />建议这样处理</h3>
          <p>{advice.answer}</p>
        </section>

        {advice.follow_up_question && (
          <section className="director-help-follow-up">
            <h3><MessageSquareText aria-hidden="true" size={18} />请先向玩家问清楚</h3>
            <p>{advice.follow_up_question}</p>
          </section>
        )}

        {advice.suggested_response && (
          <section>
            <h3><MessageSquareText aria-hidden="true" size={18} />给 KP 的措辞草稿</h3>
            <small className="director-help-disclosure-warning">
              仅供 KP 审阅，可能综合隐藏资料，不可直接念给玩家。
            </small>
            <blockquote>{advice.suggested_response}</blockquote>
          </section>
        )}

        {!!advice.next_steps.length && (
          <section>
            <h3><ClipboardList aria-hidden="true" size={18} />接下来的步骤</h3>
            {list(advice.next_steps)}
          </section>
        )}

        {advice.action && (
          <section className="director-help-action">
            <h3><Dices aria-hidden="true" size={18} />规则与检定建议</h3>
            <div className="director-help-action-heading">
              <div>
                <strong>{advice.action.title}</strong>
                <small>
                  {advice.action.policy === null
                    ? "分步任务"
                    : policyLabels[advice.action.policy]}
                </small>
              </div>
              <span className={advice.action.available ? "available" : "unavailable"}>
                {historical
                  ? (advice.action.available ? "当时可用" : "当时不可用")
                  : (advice.action.available ? "当前可用" : "当前不可用")}
              </span>
            </div>
            {advice.action.reason && <p>{advice.action.reason}</p>}
            {!!advice.action.step_operator_ids.length && (
              <p><strong>分步行动：</strong>{advice.action.step_operator_ids.join(" → ")}</p>
            )}
            {(advice.action.skill_choices.length > 0 || advice.action.skill_choices_truncated) && (
              <div className="director-help-check" aria-label="技能选项">
                <strong>技能选项</strong>
                <ul>
                  {advice.action.skill_choices.map((choice) => (
                    <li key={choice.skill_key}>
                      {choice.skill_key} · {choice.difficulty}{choice.hidden ? " · 隐藏" : ""}
                    </li>
                  ))}
                </ul>
                {advice.action.skill_choices_truncated && (
                  <small>
                    仅显示前 {advice.action.skill_choices.length}/
                    {advice.action.skill_choices_total_count} 项技能选项。
                  </small>
                )}
              </div>
            )}
            {selectedSkill && (
              <div className="director-help-check">
                <strong>建议检定：{selectedSkill.skill_key} · {selectedSkill.difficulty}</strong>
                <p>{selectedSkill.reason}</p>
                {!!selectedSkill.automatic_information.length && (
                  <><small>无需检定即可告知</small>{list(selectedSkill.automatic_information)}</>
                )}
                {selectedSkill.failure_stakes && (
                  <p><strong>失败后果：</strong>{selectedSkill.failure_stakes}</p>
                )}
                {selectedSkill.pushed_failure_stakes && (
                  <p><strong>孤注一掷失败：</strong>{selectedSkill.pushed_failure_stakes}</p>
                )}
              </div>
            )}
            {!!advice.action.automatic_information.length && (
              <><small>自动信息</small>{list(advice.action.automatic_information)}</>
            )}
            {advice.action.maximum_effect && (
              <p><strong>最大影响：</strong>{advice.action.maximum_effect}</p>
            )}
            {(advice.action.success_effects.length > 0
              || advice.action.failure_effects.length > 0
              || advice.action.success_effects_truncated
              || advice.action.failure_effects_truncated) && (
              <div className="director-help-effects" aria-label="状态变更预览">
                <strong>可能的状态变化</strong>
                <small>仅预览，尚未应用</small>
                {(advice.action.success_effects.length > 0
                  || advice.action.success_effects_truncated) && (
                  <div>
                    <span>成功时</span>
                    {list(advice.action.success_effects)}
                    {advice.action.success_effects_truncated && (
                      <small>
                        仅显示前 {advice.action.success_effects.length}/
                        {advice.action.success_effects_total_count} 项成功效果。
                      </small>
                    )}
                  </div>
                )}
                {(advice.action.failure_effects.length > 0
                  || advice.action.failure_effects_truncated) && (
                  <div>
                    <span>失败时</span>
                    {list(advice.action.failure_effects)}
                    {advice.action.failure_effects_truncated && (
                      <small>
                        仅显示前 {advice.action.failure_effects.length}/
                        {advice.action.failure_effects_total_count} 项失败效果。
                      </small>
                    )}
                  </div>
                )}
              </div>
            )}
          </section>
        )}

        {!advice.action && (
          <section className="director-help-action">
            <h3><Dices aria-hidden="true" size={18} />规则与检定建议</h3>
            <p>当前没有可安全建议的契约行动或检定；请根据上方问题补充提示或暂停推进。</p>
          </section>
        )}

        <section className="director-help-uncertainty">
          <h3><AlertTriangle aria-hidden="true" size={18} />不确定性与假设</h3>
          {advice.uncertainty_reasons.length || advice.assumptions.length ? (
            <>
              {list(advice.uncertainty_reasons)}
              {!!advice.assumptions.length && (
                <details><summary>查看所用假设</summary>{list(advice.assumptions)}</details>
              )}
            </>
          ) : <p>当前没有额外的不确定性说明。</p>}
        </section>

        <section className="director-help-sources">
          <h3><BookOpenCheck aria-hidden="true" size={18} />依据</h3>
          {advice.citations.length ? advice.citations.map((citation) => (
            <blockquote key={citation.evidence_id}>
              <div className="director-help-source-heading">
                <strong>{citation.title}</strong>
                <span className={`director-help-visibility visibility-${citation.visibility}`}>
                  {evidenceVisibilityLabels[citation.visibility]}
                </span>
              </div>
              <p>{citation.text}</p>
              <small>{citation.source_locator ?? citation.source_type}</small>
              {(citation.source_refs.length > 0 || citation.source_refs_truncated) && (
                <div aria-label={`${citation.title}的来源定位`}>
                  {!!citation.source_refs.length && (
                    <ul>
                      {citation.source_refs.map((reference) => (
                        <li key={`${reference.document_id}:${reference.source_block_id}`}>
                          {sourceReferenceLabel(reference)}
                        </li>
                      ))}
                    </ul>
                  )}
                  {citation.source_refs_truncated && (
                    <small>
                      仅显示前 {citation.source_refs.length}/
                      {citation.source_refs_total_count} 条来源定位。
                    </small>
                  )}
                </div>
              )}
            </blockquote>
          )) : (
            <p className="director-help-no-source">
              没有可核验的来源，请不要把这条建议当作模组事实。
            </p>
          )}
        </section>

        <footer>
          <span><ShieldCheck aria-hidden="true" size={15} />不可直接执行</span>
          <small title={advice.basis_hash}>
            依据版本 {advice.scenario_version}.{advice.state_version} · {advice.basis_hash.slice(0, 10)}
          </small>
        </footer>
      </article>
    );
  }
);
