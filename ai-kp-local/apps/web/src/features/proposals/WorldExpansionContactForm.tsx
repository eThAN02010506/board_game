import { CheckCircle2, History, MapPin, UserRoundPlus } from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";

import type {
  CampaignInvestigator,
  NpcReappearanceCandidate,
  SavedMap,
  TurnProposal,
  WorldExpansionEncounterInput
} from "../../api/types";

type Props = {
  activeMap: SavedMap | null;
  campaignTime: string;
  contactInvestigators: CampaignInvestigator[];
  loading: boolean;
  npcReappearanceCandidates: NpcReappearanceCandidate[];
  proposal: TurnProposal;
  onConfirm: (input: WorldExpansionEncounterInput) => void;
};

function newIdempotencyKey(proposalId: string): string {
  const suffix =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `contact:${proposalId}:${suffix}`;
}

export function WorldExpansionContactForm({
  activeMap,
  campaignTime,
  contactInvestigators,
  loading,
  npcReappearanceCandidates,
  proposal,
  onConfirm
}: Props) {
  const candidate = proposal.world_expansion?.candidate;
  const typedBindings = useMemo(
    () => candidate?.template_binding?.entity_bindings ?? [],
    [candidate?.template_binding?.entity_bindings]
  );
  const typedNpcBindings = typedBindings.filter(
    (item) => item.entity_kind === "npc"
  );
  const receipt = proposal.world_expansion_materialization;
  const [idempotencyKey, setIdempotencyKey] = useState(() =>
    newIdempotencyKey(proposal.id)
  );
  const [summary, setSummary] = useState("");
  const [happenedAt, setHappenedAt] = useState(campaignTime);
  const [subject, setSubject] = useState("");
  const [predicate, setPredicate] = useState("实际存在");
  const [objectText, setObjectText] = useState("");
  const [includeNpc, setIncludeNpc] = useState(false);
  const [npcMode, setNpcMode] = useState<"new" | "known">("new");
  const [knownNpcId, setKnownNpcId] = useState("");
  const [npcName, setNpcName] = useState("");
  const [profession, setProfession] = useState("");
  const [homeLocation, setHomeLocation] = useState("");
  const [placeOnMap, setPlaceOnMap] = useState(false);
  const [mapLocation, setMapLocation] = useState("");
  const [participantIds, setParticipantIds] = useState<string[]>([]);
  const [interactionSummary, setInteractionSummary] = useState("");
  const [professionContext, setProfessionContext] = useState("");
  const [entityValues, setEntityValues] = useState<
    Record<
      string,
      { name: string; description: string; visibility: "table" | "kp" | "secret" }
    >
  >({});

  useEffect(() => {
    setIdempotencyKey(newIdempotencyKey(proposal.id));
    setSummary(`调查员实际接触了“${candidate?.subject ?? "补全内容"}”。`);
    setHappenedAt(campaignTime);
    setSubject(candidate?.subject ?? "");
    setPredicate("实际存在");
    setObjectText(candidate?.proposal ?? "");
    setIncludeNpc(false);
    setNpcMode("new");
    setKnownNpcId("");
    setNpcName("");
    setProfession("");
    setHomeLocation("");
    setPlaceOnMap(false);
    setMapLocation(activeMap?.locations?.[0]?.name ?? "");
    setParticipantIds([]);
    setInteractionSummary("");
    setProfessionContext("");
    setEntityValues(
      Object.fromEntries(
        typedBindings.map((binding) => [
          binding.local_ref,
          {
            name: binding.label_variant,
            description: "",
            visibility: "table" as const
          }
        ])
      )
    );
  }, [
    activeMap?.id,
    activeMap?.locations,
    campaignTime,
    candidate?.proposal,
    candidate?.subject,
    proposal.id,
    typedBindings
  ]);

  if (!candidate) return null;
  if (receipt) {
    return (
      <section className="world-materialization-receipt">
        <CheckCircle2 size={19} />
        <div>
          <strong>实际接触已经写入世界</strong>
          <p>
            {receipt.fact_event_ids.length} 条严格事实
            {receipt.world_entity_ids?.length
              ? ` · ${receipt.world_entity_ids.length} 个实体已记录`
              : ""}
            {receipt.npc_id ? " · NPC 已记录" : ""}
            {receipt.map_token_id ? " · 地图棋子已同步" : ""}
          </p>
          <small>收据 {receipt.materialization_id}</small>
        </div>
      </section>
    );
  }
  if (proposal.status !== "approved") return null;

  const availableLocations = activeMap?.locations ?? [];
  const candidateSubject = candidate.subject;
  const selectedKnownNpc =
    npcReappearanceCandidates.find((item) => item.npc_id === knownNpcId) ?? null;

  function submit(event: FormEvent) {
    event.preventDefault();
    const npc = includeNpc
      ? npcMode === "known" && selectedKnownNpc
        ? {
            npc_id: selectedKnownNpc.npc_id,
            role: "reappeared"
          }
        : {
          name: npcName,
          profession: profession || null,
          home_location: homeLocation || null,
          public_notes: `首次因世界补全“${candidateSubject}”与调查员接触。`,
          role: "encountered"
          }
      : null;
    onConfirm({
      idempotency_key: idempotencyKey,
      summary,
      happened_at: happenedAt.trim() || null,
      facts: [
        {
          fact_type: "canonical_fact",
          subject,
          predicate,
          object_text: objectText
        }
      ],
      entities: typedBindings.map((binding) => ({
        local_ref: binding.local_ref,
        name: entityValues[binding.local_ref]?.name ?? "",
        description: entityValues[binding.local_ref]?.description ?? "",
        visibility: entityValues[binding.local_ref]?.visibility ?? "table"
      })),
      npc,
      participant_investigator_ids:
        includeNpc || typedNpcBindings.length > 0 ? participantIds : [],
      interaction_summary:
        (includeNpc || typedNpcBindings.length > 0) && participantIds.length
          ? interactionSummary.trim() || summary
          : null,
      profession_context:
        includeNpc && npcMode === "known"
          ? professionContext.trim() || null
          : null,
      map_placement:
        (includeNpc || typedNpcBindings.length > 0) &&
        placeOnMap &&
        activeMap &&
        mapLocation
          ? {
              map_id: activeMap.id,
              location_name: mapLocation,
              entity_ref: typedNpcBindings[0]?.local_ref ?? null,
              visibility: "table",
              color: "#b93f2d"
            }
          : null
    });
  }

  return (
    <form className="world-materialization-form" onSubmit={submit}>
      <header>
        <div>
          <span>第二次确认</span>
          <h3>玩家是否已经实际接触？</h3>
        </div>
        <small>确认后写入追加式世界状态；需要纠正时应走修订流程。</small>
      </header>
      <label>
        桌面实际发生的事情
        <textarea
          required
          value={summary}
          onChange={(event) => setSummary(event.target.value)}
        />
      </label>
      <label>
        团内时间
        <input
          value={happenedAt}
          onChange={(event) => setHappenedAt(event.target.value)}
        />
      </label>
      <fieldset>
        <legend>将要建立的严格 World Fact</legend>
        <label>
          主体
          <input required value={subject} onChange={(event) => setSubject(event.target.value)} />
        </label>
        <label>
          关系
          <input
            required
            value={predicate}
            onChange={(event) => setPredicate(event.target.value)}
          />
        </label>
        <label>
          事实内容
          <textarea
            required
            value={objectText}
            onChange={(event) => setObjectText(event.target.value)}
          />
        </label>
      </fieldset>
      {!!typedBindings.length && (
        <fieldset className="world-materialization-entities">
          <legend>获批模板实体的具体身份</legend>
          <small>类型、原型与关系已经锁定；这里仅填写桌面实际确认的信息。</small>
          {typedBindings.map((binding) => {
            const value = entityValues[binding.local_ref] ?? {
              name: "",
              description: "",
              visibility: "table" as const
            };
            return (
              <article key={binding.local_ref}>
                <strong>
                  {binding.label_variant} · {binding.entity_kind}
                </strong>
                <small>
                  {binding.archetype_id} / {binding.local_ref}
                </small>
                <label>
                  具体名称
                  <input
                    required
                    value={value.name}
                    onChange={(event) =>
                      setEntityValues((current) => ({
                        ...current,
                        [binding.local_ref]: { ...value, name: event.target.value }
                      }))
                    }
                  />
                </label>
                <label>
                  已实际确认的描述
                  <textarea
                    value={value.description}
                    onChange={(event) =>
                      setEntityValues((current) => ({
                        ...current,
                        [binding.local_ref]: {
                          ...value,
                          description: event.target.value
                        }
                      }))
                    }
                  />
                </label>
                <label>
                  可见范围
                  <select
                    value={value.visibility}
                    onChange={(event) =>
                      setEntityValues((current) => ({
                        ...current,
                        [binding.local_ref]: {
                          ...value,
                          visibility: event.target.value as "table" | "kp" | "secret"
                        }
                      }))
                    }
                  >
                    <option value="table">全桌可见</option>
                    <option value="kp">仅 KP</option>
                    <option value="secret">秘密</option>
                  </select>
                </label>
              </article>
            );
          })}
        </fieldset>
      )}
      {typedNpcBindings.length === 0 && (
      <label className="world-materialization-toggle">
        <input
          checked={includeNpc}
          type="checkbox"
          onChange={(event) => {
            setIncludeNpc(event.target.checked);
            if (!event.target.checked) setPlaceOnMap(false);
          }}
        />
        <UserRoundPlus size={16} />
        这次接触中出现了需要长期记录的 NPC
      </label>
      )}
      {includeNpc && (
        <fieldset className="world-materialization-npc">
          <legend>NPC 档案</legend>
          <div className="world-materialization-mode">
            <button
              className={npcMode === "new" ? "selected" : ""}
              onClick={() => setNpcMode("new")}
              type="button"
            >
              <UserRoundPlus size={15} />
              新 NPC
            </button>
            <button
              className={npcMode === "known" ? "selected" : ""}
              disabled={npcReappearanceCandidates.length === 0}
              onClick={() => {
                setNpcMode("known");
                const first = npcReappearanceCandidates[0];
                setKnownNpcId(first?.npc_id ?? "");
                setParticipantIds(
                  Array.from(
                    new Set(
                      first?.qualifying_investigators.map(
                        (item) => item.investigator_id
                      ) ?? []
                    )
                  )
                );
              }}
              type="button"
            >
              <History size={15} />
              旧识复现
            </button>
          </div>
          {npcMode === "new" ? (
            <>
              <label>
                姓名
                <input
                  required
                  value={npcName}
                  onChange={(event) => setNpcName(event.target.value)}
                />
              </label>
              <label>
                职业
                <input
                  value={profession}
                  onChange={(event) => setProfession(event.target.value)}
                />
              </label>
              <label>
                常驻地点
                <input
                  value={homeLocation}
                  onChange={(event) => setHomeLocation(event.target.value)}
                />
              </label>
            </>
          ) : (
            <>
              <label>
                由稳定调查员经历授权的旧识
                <select
                  required
                  value={knownNpcId}
                  onChange={(event) => {
                    const npcId = event.target.value;
                    setKnownNpcId(npcId);
                    const selected = npcReappearanceCandidates.find(
                      (item) => item.npc_id === npcId
                    );
                    setParticipantIds(
                      Array.from(
                        new Set(
                          selected?.qualifying_investigators.map(
                            (item) => item.investigator_id
                          ) ?? []
                        )
                      )
                    );
                  }}
                >
                  {npcReappearanceCandidates.map((item) => (
                    <option key={item.npc_id} value={item.npc_id}>
                      {item.name}
                      {item.profession ? ` · ${item.profession}` : ""}
                    </option>
                  ))}
                </select>
              </label>
              {selectedKnownNpc?.qualifying_investigators.map((item) => (
                <article
                  className="world-materialization-history"
                  key={`${item.investigator_id}:${item.interaction_summary}`}
                >
                  <strong>{item.investigator_name}记得此人</strong>
                  <p>{item.interaction_summary}</p>
                  {item.happened_at && <small>{item.happened_at}</small>}
                </article>
              ))}
              {selectedKnownNpc && (
                <aside
                  className={`world-appearance-gate ${selectedKnownNpc.appearance_gate.decision}`}
                >
                  <strong>
                    {selectedKnownNpc.appearance_gate.decision === "eligible"
                      ? "确定性门控通过"
                      : "需要 KP 复核"}
                  </strong>
                  {selectedKnownNpc.appearance_gate.reasons.map((reason) => (
                    <p key={reason}>✓ {reason}</p>
                  ))}
                  {selectedKnownNpc.appearance_gate.warnings.map((warning) => (
                    <p key={warning}>! {warning}</p>
                  ))}
                  <small>
                    本团旧识余额{" "}
                    {selectedKnownNpc.appearance_gate.remaining_campaign_budget}/
                    {selectedKnownNpc.appearance_gate.max_returning_npcs}
                  </small>
                </aside>
              )}
              <label>
                本次寻找的行业或身份
                <input
                  value={professionContext}
                  onChange={(event) => setProfessionContext(event.target.value)}
                  placeholder="例如：报社线人、警务人员"
                />
              </label>
            </>
          )}
          <fieldset>
            <legend>本次实际接触的调查员</legend>
            {contactInvestigators.map((item) => (
              <label
                className="world-materialization-toggle"
                key={item.investigator_id}
              >
                <input
                  checked={participantIds.includes(item.investigator_id)}
                  type="checkbox"
                  onChange={(event) =>
                    setParticipantIds((current) =>
                      event.target.checked
                        ? [...current, item.investigator_id]
                        : current.filter(
                            (investigatorId) =>
                              investigatorId !== item.investigator_id
                          )
                    )
                  }
                />
                {item.name}
              </label>
            ))}
            {contactInvestigators.length === 0 && (
              <small>当前团还没有已批准的稳定调查员；本次不会建立跨本人物经历。</small>
            )}
            {!!participantIds.length && (
              <label>
                他们一起做了什么
                <textarea
                  value={interactionSummary}
                  onChange={(event) => setInteractionSummary(event.target.value)}
                  placeholder={summary}
                />
              </label>
            )}
          </fieldset>
          <label className="world-materialization-toggle">
            <input
              checked={placeOnMap}
              disabled={!activeMap || availableLocations.length === 0}
              type="checkbox"
              onChange={(event) => setPlaceOnMap(event.target.checked)}
            />
            <MapPin size={16} />
            同步到当前地图
          </label>
          {placeOnMap && activeMap && (
            <label>
              {activeMap.title}上的已有地点
              <select
                required
                value={mapLocation}
                onChange={(event) => setMapLocation(event.target.value)}
              >
                {availableLocations.map((location) => (
                  <option key={location.id} value={location.name}>
                    {location.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {!activeMap && (
            <small>当前没有地图；NPC 与事实仍可先保存，之后再放置棋子。</small>
          )}
        </fieldset>
      )}
      {typedNpcBindings.length > 0 && (
        <fieldset className="world-materialization-npc">
          <legend>模板 NPC 的接触记录</legend>
          <fieldset>
            <legend>本次实际接触的调查员</legend>
            {contactInvestigators.map((item) => (
              <label
                className="world-materialization-toggle"
                key={item.investigator_id}
              >
                <input
                  checked={participantIds.includes(item.investigator_id)}
                  type="checkbox"
                  onChange={(event) =>
                    setParticipantIds((current) =>
                      event.target.checked
                        ? [...current, item.investigator_id]
                        : current.filter(
                            (investigatorId) => investigatorId !== item.investigator_id
                          )
                    )
                  }
                />
                {item.name}
              </label>
            ))}
            {contactInvestigators.length === 0 && (
              <small>当前团还没有已批准的稳定调查员；不会建立跨本人物经历。</small>
            )}
            {!!participantIds.length && (
              <label>
                他们一起做了什么
                <textarea
                  value={interactionSummary}
                  onChange={(event) => setInteractionSummary(event.target.value)}
                  placeholder={summary}
                />
              </label>
            )}
          </fieldset>
          <label className="world-materialization-toggle">
            <input
              checked={placeOnMap}
              disabled={!activeMap || availableLocations.length === 0}
              type="checkbox"
              onChange={(event) => setPlaceOnMap(event.target.checked)}
            />
            <MapPin size={16} />
            将第一个模板 NPC 同步到当前地图
          </label>
          {placeOnMap && activeMap && (
            <label>
              {activeMap.title}上的已有地点
              <select
                required
                value={mapLocation}
                onChange={(event) => setMapLocation(event.target.value)}
              >
                {availableLocations.map((location) => (
                  <option key={location.id} value={location.name}>
                    {location.name}
                  </option>
                ))}
              </select>
            </label>
          )}
        </fieldset>
      )}
      <button className="primary-button" disabled={loading} type="submit">
        <CheckCircle2 size={16} />
        确认实际接触并原子落地
      </button>
    </form>
  );
}
