import {
  Activity,
  Crosshair,
  Footprints,
  RefreshCw
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { requestJson } from "../../api/client";
import type {
  AuthIdentity,
  Campaign,
  CampaignInvestigator,
  Coc7CharacterGameplayState,
  Coc7Encounter,
  Coc7GameplayEvent
} from "../../api/types";
import {
  CharacterStatePanel,
  type CharacterCommand,
  type CharacterDraft
} from "./CharacterStatePanel";

type Props = {
  campaign: Campaign | null;
  identity: AuthIdentity | null;
};

type CommandResponse = {
  encounter?: Coc7Encounter;
  event: Coc7GameplayEvent;
  state?: Coc7CharacterGameplayState["state"];
  idempotent_replay: boolean;
  permanent_change?: { id: string; status: string } | null;
};

function commandId(prefix: string) {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`;
}

export function GameplayWorkbench({ campaign, identity }: Props) {
  const { t } = useTranslation();
  const [encounters, setEncounters] = useState<Coc7Encounter[]>([]);
  const [investigators, setInvestigators] = useState<CampaignInvestigator[]>([]);
  const [selectedEncounterId, setSelectedEncounterId] = useState("");
  const [selectedInvestigatorId, setSelectedInvestigatorId] = useState("");
  const [characterState, setCharacterState] =
    useState<Coc7CharacterGameplayState | null>(null);
  const [events, setEvents] = useState<Coc7GameplayEvent[]>([]);
  const [message, setMessage] = useState(t("gameplay.messageInitial"));
  const [busy, setBusy] = useState(false);
  const [kind, setKind] = useState<"combat" | "chase">("combat");
  const [title, setTitle] = useState(t("gameplay.newSceneTitle"));
  const [selectedParticipants, setSelectedParticipants] = useState<string[]>([]);
  const [npcName, setNpcName] = useState(t("gameplay.unknownThreat"));
  const [npcDex, setNpcDex] = useState("55");
  const [npcHp, setNpcHp] = useState("10");
  const [npcMove, setNpcMove] = useState("8");
  const [locations, setLocations] = useState(t("gameplay.locationsDefault"));
  const [targetParticipantId, setTargetParticipantId] = useState("");
  const [damage, setDamage] = useState("1");
  const [attackDraft, setAttackDraft] = useState({
    attackerId: "",
    targetId: "",
    attackerTarget: "50",
    attackerRoll: "50",
    defenderTarget: "50",
    defenderRoll: "50",
    attackerBuild: "0",
    defenderBuild: "0",
    maneuverEffect: t("gameplay.maneuverEffectDefault"),
    defense: "dodge" as "dodge" | "fight_back"
  });
  const [chaseDraft, setChaseDraft] = useState({
    participantId: "",
    steps: "1",
    passed: true,
    failureCost: "1",
    damage: "0"
  });
  const [characterCommand, setCharacterCommand] =
    useState<CharacterCommand>("damage");
  const [characterDraft, setCharacterDraft] = useState<CharacterDraft>({
    amount: "1",
    passed: true,
    sanityRoll: "50",
    successLoss: "0",
    failureLoss: "1",
    intelligenceRoll: "100",
    skillKey: "",
    developmentRoll: "50",
    increaseRoll: "1",
    periodId: new Date().toISOString().slice(0, 10),
    conSuccessLevel: "regular"
  });

  const selectedEncounter = useMemo(
    () => encounters.find((item) => item.id === selectedEncounterId) ?? null,
    [encounters, selectedEncounterId]
  );
  const approved = useMemo(
    () => investigators.filter((item) => item.status === "approved" && item.campaign_state),
    [investigators]
  );
  const selectedRecord = useMemo(
    () =>
      investigators.find(
        (item) => item.investigator_id === selectedInvestigatorId
      ) ?? null,
    [investigators, selectedInvestigatorId]
  );
  const growthSkills = useMemo(() => {
    const skills = selectedRecord?.approved_revision?.canonical_sheet.skills ?? [];
    const runtimeKeys = new Set(
      (characterState?.state.conditions ?? [])
        .filter((item) => item.type === "skill_growth_mark")
        .map(
          (item) =>
            `${String(item.skill_key ?? "")}:${String(item.specialization ?? "")}`
        )
    );
    return skills.filter(
      (item) =>
        item.growth_mark ||
        runtimeKeys.has(
          `${item.skill_key}:${String(item.specialization ?? "")}`
        )
    );
  }, [characterState?.state.conditions, selectedRecord]);

  async function load(silent = false) {
    if (!campaign || !identity) return;
    if (!silent) setBusy(true);
    try {
      const investigatorPath =
        identity.role === "kp"
          ? `/campaigns/${campaign.id}/investigator-submissions`
          : `/campaigns/${campaign.id}/my-investigators`;
      const [nextEncounters, nextInvestigators] = await Promise.all([
        requestJson<Coc7Encounter[]>(`/campaigns/${campaign.id}/coc7/encounters`),
        requestJson<CampaignInvestigator[]>(investigatorPath)
      ]);
      setEncounters(nextEncounters);
      setInvestigators(nextInvestigators);
      setSelectedEncounterId((current) =>
        nextEncounters.some((item) => item.id === current)
          ? current
          : nextEncounters[0]?.id ?? ""
      );
      setSelectedInvestigatorId((current) =>
        nextInvestigators.some((item) => item.investigator_id === current)
          ? current
          : nextInvestigators.find((item) => item.status === "approved")
              ?.investigator_id ?? ""
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      if (!silent) setBusy(false);
    }
  }

  async function loadEncounterDetails(encounter: Coc7Encounter | null) {
    if (!encounter) {
      setEvents([]);
      return;
    }
    try {
      const next = await requestJson<Coc7GameplayEvent[]>(
        `/coc7/encounters/${encounter.id}/events`
      );
      setEvents(next);
      const first = encounter.state.participants[0]?.participant_id ?? "";
      const second = encounter.state.participants[1]?.participant_id ?? first;
      setTargetParticipantId((current) => current || first);
      setAttackDraft((current) => ({
        ...current,
        attackerId: current.attackerId || first,
        targetId: current.targetId || second
      }));
      setChaseDraft((current) => ({
        ...current,
        participantId: current.participantId || first
      }));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function loadCharacter(investigatorId: string) {
    if (!campaign || !investigatorId) {
      setCharacterState(null);
      return;
    }
    try {
      const next = await requestJson<Coc7CharacterGameplayState>(
        `/campaigns/${campaign.id}/investigators/${investigatorId}/coc7/state`
      );
      setCharacterState(next);
      const record = investigators.find(
        (item) => item.investigator_id === investigatorId
      );
      const runtimeKeys = new Set(
        next.state.conditions
          .filter((item) => item.type === "skill_growth_mark")
          .map(
            (item) =>
              `${String(item.skill_key ?? "")}:${String(item.specialization ?? "")}`
          )
      );
      const firstMarkedSkill =
        record?.approved_revision?.canonical_sheet.skills?.find(
          (item) =>
            item.growth_mark ||
            runtimeKeys.has(
              `${item.skill_key}:${String(item.specialization ?? "")}`
            )
        );
      if (firstMarkedSkill) {
        setCharacterDraft((current) => ({
          ...current,
          skillKey: firstMarkedSkill.skill_key
        }));
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  useEffect(() => {
    void load();
  }, [campaign?.id, identity?.member_id, identity?.role]);

  useEffect(() => {
    void loadEncounterDetails(selectedEncounter);
  }, [selectedEncounter?.id, selectedEncounter?.version]);

  useEffect(() => {
    void loadCharacter(selectedInvestigatorId);
  }, [campaign?.id, selectedInvestigatorId, investigators.length]);

  if (!campaign || !identity) {
    return (
      <section className="tool-panel gameplay-workbench">
        <h2>{t("gameplay.stateMachineTitle")}</h2>
        <p>{t("gameplay.joinHint")}</p>
      </section>
    );
  }
  const activeCampaign = campaign;
  const activeIdentity = identity;

  async function runAction(label: string, action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      setMessage(t("gameplay.saved", { label }));
      await load(true);
      if (selectedEncounter) await loadEncounterDetails(selectedEncounter);
      if (selectedInvestigatorId) await loadCharacter(selectedInvestigatorId);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function createEncounter() {
    if (activeIdentity.role !== "kp") return;
    const linked = approved
      .filter((item) => selectedParticipants.includes(item.investigator_id))
      .map((item, index) => ({
        participant_id: `investigator-${item.investigator_id}`,
        name: item.name,
        investigator_id: item.investigator_id,
        chase_role: (index === 0 ? "fleeing" : "pursuer") as
          | "fleeing"
          | "pursuer",
        location_index: index === 0 ? 1 : 0
      }));
    const participantPayload = [
      ...linked,
      {
        participant_id: `npc-${Date.now()}`,
        name: npcName,
        dex: Number(npcDex),
        move: Number(npcMove),
        max_hp: Number(npcHp),
        current_hp: Number(npcHp),
        chase_role: "pursuer",
        location_index: 0
      }
    ];
    if (kind === "chase" && !participantPayload.some((item) => item.chase_role === "fleeing")) {
      participantPayload[0].chase_role = "fleeing";
    }
    await requestJson(`/campaigns/${activeCampaign.id}/coc7/encounters`, {
      method: "POST",
      body: JSON.stringify({
        kind,
        title,
        participants: participantPayload,
        locations:
          kind === "chase"
            ? locations
                .split(/[,，]/)
                .map((label) => label.trim())
                .filter(Boolean)
                .map((label) => ({ label }))
            : []
      })
    });
  }

  async function encounterCommand(
    commandType: string,
    payload: Record<string, unknown>
  ) {
    if (!selectedEncounter) return;
    await requestJson<CommandResponse>(
      `/coc7/encounters/${selectedEncounter.id}/commands`,
      {
        method: "POST",
        body: JSON.stringify({
          command_id: commandId(commandType),
          expected_version: selectedEncounter.version,
          command_type: commandType,
          payload
        })
      }
    );
  }

  async function characterStateCommand() {
    if (!selectedInvestigatorId || !characterState) return;
    let payload: Record<string, unknown>;
    if (characterCommand === "damage") {
      payload = { damage: Number(characterDraft.amount) };
    } else if (
      characterCommand === "major_wound_con" ||
      characterCommand === "dying_con" ||
      characterCommand === "first_aid"
    ) {
      payload = { passed: characterDraft.passed };
    } else if (characterCommand === "medicine") {
      payload = {
        passed: characterDraft.passed,
        healing: Number(characterDraft.amount)
      };
    } else if (characterCommand === "natural_healing") {
      payload = {
        period_id: characterDraft.periodId,
        days: Number(characterDraft.amount),
        con_success_level: characterDraft.conSuccessLevel
      };
    } else if (characterCommand === "sanity") {
      payload = {
        sanity_roll: Number(characterDraft.sanityRoll),
        success_loss: Number(characterDraft.successLoss),
        failure_loss: Number(characterDraft.failureLoss),
        intelligence_roll: Number(characterDraft.intelligenceRoll)
      };
    } else if (characterCommand === "development") {
      payload = {
        skill_key: characterDraft.skillKey,
        development_roll: Number(characterDraft.developmentRoll),
        increase_roll: Number(characterDraft.increaseRoll)
      };
    } else if (characterCommand === "reset_san_day") {
      payload = { day: new Date().toISOString().slice(0, 10) };
    } else {
      payload = {};
    }
    await requestJson(
      `/campaigns/${activeCampaign.id}/investigators/${selectedInvestigatorId}/coc7/commands`,
      {
        method: "POST",
        body: JSON.stringify({
          command_id: commandId(characterCommand),
          expected_version: characterState.state.state_version,
          command_type: characterCommand,
          payload
        })
      }
    );
  }

  return (
    <section className="tool-panel gameplay-workbench">
      <header className="panel-heading">
        <div>
          <p className="eyebrow">{t("gameplay.eyebrow")}</p>
          <h2>{t("gameplay.title")}</h2>
        </div>
        <Activity size={19} />
      </header>
      <div className="gameplay-toolbar">
        <button
          className="ghost-button"
          disabled={busy}
          onClick={() => void load()}
          type="button"
        >
          <RefreshCw size={14} />{t("gameplay.refresh")}
        </button>
        <span>{message}</span>
      </div>

      <div className="gameplay-vitals">
        {approved.map((record) => (
          <button
            className={
              selectedInvestigatorId === record.investigator_id ? "active" : ""
            }
            key={record.investigator_id}
            onClick={() => setSelectedInvestigatorId(record.investigator_id)}
            type="button"
          >
            <strong>{record.name}</strong>
            <span>
              HP {record.campaign_state?.current_hp ?? "-"} · SAN{" "}
              {record.campaign_state?.current_san ?? "-"}
            </span>
          </button>
        ))}
      </div>

      {characterState && (
        <CharacterStatePanel
          busy={busy}
          command={characterCommand}
          draft={characterDraft}
          growthSkills={growthSkills}
          isKeeper={identity.role === "kp"}
          onExecute={() =>
            void runAction(t("gameplay.actionCharacterState"), characterStateCommand)
          }
          setCommand={setCharacterCommand}
          setDraft={setCharacterDraft}
          state={characterState}
        />
      )}

      {identity.role === "kp" && (
        <details className="gameplay-create">
          <summary>{t("gameplay.createEncounter")}</summary>
          <div className="gameplay-command-grid">
            <label>
              {t("gameplay.type")}
              <select
                value={kind}
                onChange={(event) =>
                  setKind(event.target.value as "combat" | "chase")
                }
              >
                <option value="combat">{t("gameplay.combat")}</option>
                <option value="chase">{t("gameplay.chase")}</option>
              </select>
            </label>
            <label>
              {t("gameplay.encounterTitle")}
              <input value={title} onChange={(event) => setTitle(event.target.value)} />
            </label>
            <label>
              {t("gameplay.threatLabel")}
              <input
                value={npcName}
                onChange={(event) => setNpcName(event.target.value)}
              />
            </label>
            <label>
              {t("gameplay.npcDex")}
              <input
                min={0}
                type="number"
                value={npcDex}
                onChange={(event) => setNpcDex(event.target.value)}
              />
            </label>
            <label>
              {t("gameplay.npcHp")}
              <input
                min={1}
                type="number"
                value={npcHp}
                onChange={(event) => setNpcHp(event.target.value)}
              />
            </label>
            {kind === "chase" && (
              <>
                <label>
                  {t("gameplay.npcMov")}
                  <input
                    min={0}
                    type="number"
                    value={npcMove}
                    onChange={(event) => setNpcMove(event.target.value)}
                  />
                </label>
                <label className="wide">
                  {t("gameplay.locationChain")}
                  <input
                    value={locations}
                    onChange={(event) => setLocations(event.target.value)}
                  />
                </label>
              </>
            )}
          </div>
          <div className="participant-picker">
            {approved.map((record) => (
              <label key={record.investigator_id}>
                <input
                  checked={selectedParticipants.includes(record.investigator_id)}
                  onChange={(event) =>
                    setSelectedParticipants((items) =>
                      event.target.checked
                        ? [...items, record.investigator_id]
                        : items.filter((id) => id !== record.investigator_id)
                    )
                  }
                  type="checkbox"
                />
                {record.name}
              </label>
            ))}
          </div>
          <button
            className="primary-button"
            disabled={busy || !selectedParticipants.length}
            onClick={() => void runAction(t("gameplay.actionCreateEncounter"), createEncounter)}
            type="button"
          >
            {kind === "combat" ? <Crosshair size={14} /> : <Footprints size={14} />}
            {kind === "combat" ? t("gameplay.createCombat") : t("gameplay.createChase")}
          </button>
        </details>
      )}

      <div className="encounter-selector">
        {encounters.map((encounter) => (
          <button
            className={selectedEncounterId === encounter.id ? "active" : ""}
            key={encounter.id}
            onClick={() => setSelectedEncounterId(encounter.id)}
            type="button"
          >
            <strong>{encounter.title}</strong>
            <span>
              {encounter.kind === "combat" ? t("gameplay.combat") : t("gameplay.chase")} · {t("gameplay.roundLabel", { round: encounter.round_no })} · {encounter.status}
            </span>
          </button>
        ))}
      </div>

      {selectedEncounter && (
        <article className="encounter-console">
          <header>
            <div>
              <strong>{selectedEncounter.title}</strong>
              <small>
                {t("gameplay.versionLabel", { version: selectedEncounter.version })} · {t("gameplay.currentSeat")}{" "}
                {selectedEncounter.state.turn_order[selectedEncounter.turn_index] ??
                  "—"}
              </small>
            </div>
            {identity.role === "kp" && selectedEncounter.status === "active" && (
              <span className="encounter-header-actions">
                <button
                  className="ghost-button"
                  disabled={busy}
                  onClick={() =>
                    void runAction(t("gameplay.actionAdvanceTurn"), () =>
                      encounterCommand("advance_turn", {})
                    )
                  }
                  type="button"
                >
                  {t("gameplay.advanceTurn")}
                </button>
                <button
                  className="ghost-button"
                  disabled={busy}
                  onClick={() =>
                    void runAction(t("gameplay.actionEndEncounter"), () =>
                      encounterCommand("complete", {})
                    )
                  }
                  type="button"
                >
                  {t("gameplay.endEncounter")}
                </button>
              </span>
            )}
          </header>
          <div className="encounter-participants">
            {selectedEncounter.state.participants.map((participant) => (
              <div key={participant.participant_id}>
                <strong>{participant.name}</strong>
                <span>
                  DEX {participant.dex}
                  {participant.current_hp !== undefined
                    ? ` · HP ${participant.current_hp}/${participant.max_hp}`
                    : ""}
                  {participant.action_points !== undefined
                    ? ` · AP ${participant.action_points}`
                    : ""}
                </span>
                {participant.location_index !== undefined &&
                  selectedEncounter.state.locations && (
                    <small>
                      {selectedEncounter.state.locations[participant.location_index]
                        ?.label ?? t("gameplay.locationUnknown")}
                    </small>
                  )}
              </div>
            ))}
          </div>

          {identity.role === "kp" && selectedEncounter.status === "active" && (
            <details>
              <summary>
                {selectedEncounter.kind === "combat" ? t("gameplay.resolveCombat") : t("gameplay.resolveChase")}
              </summary>
              {selectedEncounter.kind === "combat" ? (
                <div className="gameplay-command-stack">
                  <div className="gameplay-command-grid">
                    <label>
                      {t("gameplay.directDamageTarget")}
                      <select
                        value={targetParticipantId}
                        onChange={(event) =>
                          setTargetParticipantId(event.target.value)
                        }
                      >
                        {selectedEncounter.state.participants.map((item) => (
                          <option key={item.participant_id} value={item.participant_id}>
                            {item.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      {t("gameplay.damage")}
                      <input
                        min={0}
                        type="number"
                        value={damage}
                        onChange={(event) => setDamage(event.target.value)}
                      />
                    </label>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void runAction(t("gameplay.actionDamage"), () =>
                          encounterCommand("damage", {
                            target_id: targetParticipantId,
                            damage: Number(damage)
                          })
                        )
                      }
                      type="button"
                    >
                      {t("gameplay.applyDamage")}
                    </button>
                  </div>
                  <div className="gameplay-command-grid">
                    <label>
                      {t("gameplay.attacker")}
                      <select
                        value={attackDraft.attackerId}
                        onChange={(event) =>
                          setAttackDraft((value) => ({
                            ...value,
                            attackerId: event.target.value
                          }))
                        }
                      >
                        {selectedEncounter.state.participants.map((item) => (
                          <option key={item.participant_id} value={item.participant_id}>
                            {item.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      {t("gameplay.target")}
                      <select
                        value={attackDraft.targetId}
                        onChange={(event) =>
                          setAttackDraft((value) => ({
                            ...value,
                            targetId: event.target.value
                          }))
                        }
                      >
                        {selectedEncounter.state.participants.map((item) => (
                          <option key={item.participant_id} value={item.participant_id}>
                            {item.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      {t("gameplay.attackSkill")}
                      <span className="paired-inputs">
                        <input
                          type="number"
                          value={attackDraft.attackerTarget}
                          onChange={(event) =>
                            setAttackDraft((value) => ({
                              ...value,
                              attackerTarget: event.target.value
                            }))
                          }
                        />
                        <input
                          type="number"
                          value={attackDraft.attackerRoll}
                          onChange={(event) =>
                            setAttackDraft((value) => ({
                              ...value,
                              attackerRoll: event.target.value
                            }))
                          }
                        />
                      </span>
                    </label>
                    <label>
                      {t("gameplay.defenseSkill")}
                      <span className="paired-inputs">
                        <input
                          type="number"
                          value={attackDraft.defenderTarget}
                          onChange={(event) =>
                            setAttackDraft((value) => ({
                              ...value,
                              defenderTarget: event.target.value
                            }))
                          }
                        />
                        <input
                          type="number"
                          value={attackDraft.defenderRoll}
                          onChange={(event) =>
                            setAttackDraft((value) => ({
                              ...value,
                              defenderRoll: event.target.value
                            }))
                          }
                        />
                      </span>
                    </label>
                    <label>
                      {t("gameplay.defense")}
                      <select
                        value={attackDraft.defense}
                        onChange={(event) =>
                          setAttackDraft((value) => ({
                            ...value,
                            defense: event.target.value as "dodge" | "fight_back"
                          }))
                        }
                      >
                        <option value="dodge">{t("gameplay.dodge")}</option>
                        <option value="fight_back">{t("gameplay.fightBack")}</option>
                      </select>
                    </label>
                    <label>
                      {t("gameplay.build")}
                      <span className="paired-inputs">
                        <input
                          aria-label={t("gameplay.attackerBuild")}
                          type="number"
                          value={attackDraft.attackerBuild}
                          onChange={(event) =>
                            setAttackDraft((value) => ({
                              ...value,
                              attackerBuild: event.target.value
                            }))
                          }
                        />
                        <input
                          aria-label={t("gameplay.defenderBuild")}
                          type="number"
                          value={attackDraft.defenderBuild}
                          onChange={(event) =>
                            setAttackDraft((value) => ({
                              ...value,
                              defenderBuild: event.target.value
                            }))
                          }
                        />
                      </span>
                    </label>
                    <label>
                      {t("gameplay.maneuverEffect")}
                      <input
                        value={attackDraft.maneuverEffect}
                        onChange={(event) =>
                          setAttackDraft((value) => ({
                            ...value,
                            maneuverEffect: event.target.value
                          }))
                        }
                      />
                    </label>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void runAction(t("gameplay.actionMelee"), () =>
                          encounterCommand("melee", {
                            attacker_id: attackDraft.attackerId,
                            target_id: attackDraft.targetId,
                            attacker_target: Number(attackDraft.attackerTarget),
                            attacker_roll: Number(attackDraft.attackerRoll),
                            defender_target: Number(attackDraft.defenderTarget),
                            defender_roll: Number(attackDraft.defenderRoll),
                            defense: attackDraft.defense,
                            attacker_bonus_dice:
                              (selectedEncounter.state.defense_reactions?.[
                                attackDraft.targetId
                              ] ?? 0) > 0
                                ? 1
                                : 0,
                            damage: Number(damage)
                          })
                        )
                      }
                      type="button"
                    >
                      {t("gameplay.resolveMelee")}
                    </button>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void runAction(t("gameplay.actionManeuver"), () =>
                          encounterCommand("maneuver", {
                            attacker_id: attackDraft.attackerId,
                            target_id: attackDraft.targetId,
                            attacker_target: Number(attackDraft.attackerTarget),
                            attacker_roll: Number(attackDraft.attackerRoll),
                            attacker_build: Number(attackDraft.attackerBuild),
                            defender_target: Number(attackDraft.defenderTarget),
                            defender_roll: Number(attackDraft.defenderRoll),
                            defender_build: Number(attackDraft.defenderBuild),
                            defense: attackDraft.defense,
                            effect: attackDraft.maneuverEffect,
                            attacker_bonus_dice:
                              (selectedEncounter.state.defense_reactions?.[
                                attackDraft.targetId
                              ] ?? 0) > 0
                                ? 1
                                : 0
                          })
                        )
                      }
                      type="button"
                    >
                      {t("gameplay.resolveManeuver")}
                    </button>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void runAction(t("gameplay.actionFirearm"), () =>
                          encounterCommand("firearm", {
                            attacker_id: attackDraft.attackerId,
                            target_id: attackDraft.targetId,
                            attacker_target: Number(attackDraft.attackerTarget),
                            attacker_roll: Number(attackDraft.attackerRoll),
                            damage: Number(damage)
                          })
                        )
                      }
                      type="button"
                    >
                      {t("gameplay.resolveFirearm")}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="gameplay-command-stack">
                  <div className="gameplay-command-grid">
                    <label>
                      {t("gameplay.participant")}
                      <select
                        value={chaseDraft.participantId}
                        onChange={(event) =>
                          setChaseDraft((value) => ({
                            ...value,
                            participantId: event.target.value
                          }))
                        }
                      >
                        {selectedEncounter.state.participants.map((item) => (
                          <option key={item.participant_id} value={item.participant_id}>
                            {item.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      {t("gameplay.stepsLabel")}
                      <input
                        min={1}
                        type="number"
                        value={chaseDraft.steps}
                        onChange={(event) =>
                          setChaseDraft((value) => ({
                            ...value,
                            steps: event.target.value
                          }))
                        }
                      />
                    </label>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void runAction(t("gameplay.actionChaseMove"), () =>
                          encounterCommand("move", {
                            participant_id: chaseDraft.participantId,
                            steps: Number(chaseDraft.steps),
                            direction: "forward"
                          })
                        )
                      }
                      type="button"
                    >
                      {t("gameplay.move")}
                    </button>
                  </div>
                  <div className="gameplay-command-grid">
                    <label className="inline-check">
                      <input
                        checked={chaseDraft.passed}
                        onChange={(event) =>
                          setChaseDraft((value) => ({
                            ...value,
                            passed: event.target.checked
                          }))
                        }
                        type="checkbox"
                      />
                      {t("gameplay.hazardPassed")}
                    </label>
                    <label>
                      {t("gameplay.failureCost")}
                      <input
                        min={0}
                        type="number"
                        value={chaseDraft.failureCost}
                        onChange={(event) =>
                          setChaseDraft((value) => ({
                            ...value,
                            failureCost: event.target.value
                          }))
                        }
                      />
                    </label>
                    <label>
                      {t("gameplay.failureDamage")}
                      <input
                        min={0}
                        type="number"
                        value={chaseDraft.damage}
                        onChange={(event) =>
                          setChaseDraft((value) => ({
                            ...value,
                            damage: event.target.value
                          }))
                        }
                      />
                    </label>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void runAction(t("gameplay.actionHazard"), () =>
                          encounterCommand("hazard", {
                            participant_id: chaseDraft.participantId,
                            passed: chaseDraft.passed,
                            failure_action_cost: Number(chaseDraft.failureCost),
                            damage: Number(chaseDraft.damage)
                          })
                        )
                      }
                      type="button"
                    >
                      {t("gameplay.resolveHazard")}
                    </button>
                  </div>
                </div>
              )}
            </details>
          )}
          <ol className="gameplay-event-list">
            {events
              .slice()
              .reverse()
              .map((event) => (
                <li key={event.id}>
                  <strong>{event.event_type}</strong>
                  <span>
                    v{event.aggregate_version} · {event.visibility}
                  </span>
                  <small>{JSON.stringify(event.result)}</small>
                </li>
              ))}
          </ol>
        </article>
      )}
    </section>
  );
}
