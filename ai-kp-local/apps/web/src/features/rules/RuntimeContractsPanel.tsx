import { Bot, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";

import {
  fetchInstalledAiSkills,
  fetchInstalledRulesets
} from "../../api/client";
import type {
  AiSkillManifest,
  InstalledRuleset
} from "../../api/types";

const supportLevelLabels: Record<InstalledRuleset["support_level"], string> = {
  knowledge_only: "知识库",
  assisted: "辅助",
  playable_alpha: "可玩 Alpha",
  verified_playable: "验证可玩"
};

export function RuntimeContractsPanel() {
  const [rulesets, setRulesets] = useState<InstalledRuleset[]>([]);
  const [skills, setSkills] = useState<AiSkillManifest[]>([]);
  const [message, setMessage] = useState("正在读取规则系统与 AI Skill……");

  useEffect(() => {
    let active = true;
    void Promise.allSettled([
      fetchInstalledRulesets(),
      fetchInstalledAiSkills()
    ]).then(([rulesetResult, skillResult]) => {
      if (!active) return;
      const loadedRulesets = rulesetResult.status === "fulfilled"
        ? rulesetResult.value
        : [];
      const loadedSkills = skillResult.status === "fulfilled"
        ? skillResult.value
        : [];
      setRulesets(loadedRulesets);
      setSkills(loadedSkills);
      const errors = [
        rulesetResult.status === "rejected" ? rulesetResult.reason : null,
        skillResult.status === "rejected" ? skillResult.reason : null
      ].filter(Boolean);
      setMessage(
        errors.length
          ? errors.map((error) => error instanceof Error ? error.message : String(error)).join("；")
          : `本机安装了 ${loadedRulesets.length} 个规则系统、${loadedSkills.length} 个提案型 Skill。`
      );
    });
    return () => {
      active = false;
    };
  }, []);

  return (
    <section className="page-card ruleset-status-card">
      <div className="page-intro">
        <div>
          <p className="eyebrow">显式安装 · 版本固定</p>
          <h2>运行时契约</h2>
        </div>
        <ShieldCheck size={24} />
      </div>
      <div className="ruleset-status-list">
        {rulesets.map((ruleset) => (
          <article className="ruleset-status-item" key={ruleset.ruleset_id}>
            <header>
              <div>
                <strong>{ruleset.display_name}</strong>
                <small>{ruleset.engine_family}</small>
              </div>
              <span>{supportLevelLabels[ruleset.support_level]}</span>
            </header>
            <dl>
              <div><dt>规则版本</dt><dd>{ruleset.version}</dd></div>
              <div><dt>角色 Schema</dt><dd>{ruleset.character_schema_version}</dd></div>
              <div><dt>事件 Schema</dt><dd>{ruleset.event_schema_version}</dd></div>
              <div><dt>能力</dt><dd>{ruleset.capabilities.length} 项</dd></div>
            </dl>
            <p className="permission-hint">{ruleset.license.content_scope}</p>
          </article>
        ))}
      </div>
      <div className="ai-skill-contracts">
        <div className="ai-skill-contracts-heading">
          <Bot size={17} />
          <strong>提案型 AI Skill</strong>
        </div>
        <div className="ai-skill-chip-list">
          {skills.map((skill) => (
            <span key={skill.skill_id} title={skill.description}>
              {skill.display_name} · {skill.authority === "proposal_only" ? "只提案" : skill.authority}
            </span>
          ))}
        </div>
      </div>
      <p className="inline-message">{message}</p>
      <p className="permission-hint">
        上传规则书只会增加隔离知识库；未安装系统不能执行角色、检定或状态变更，提取出的
        Skill 指南也不会自动取得权威写入权限。
      </p>
    </section>
  );
}
