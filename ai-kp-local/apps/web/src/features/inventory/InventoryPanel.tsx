import { PackageOpen, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  commandInventoryItem,
  createInventoryItem,
  createInventoryOffer,
  decideInventoryOffer,
  getInventory,
  tradeInventoryItem
} from "../../api/client";
import type { AuthIdentity, InventoryItem, InventoryState } from "../../api/types";
import { useIdentityRequestScope } from "../../app/hooks/useIdentityRequestScope";

const POLL_INTERVAL_MS = 5000;

function commandId(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

export function InventoryPanel(props: {
  campaignId: string;
  identity: AuthIdentity;
  refreshKey?: string;
}) {
  const { enabled, generation, key: scopeKey, trackerRef } =
    useIdentityRequestScope(props.campaignId, props.identity);
  const [view, setView] = useState<InventoryState | null>(null);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [lootName, setLootName] = useState("");
  const [lootDescription, setLootDescription] = useState("");
  const [lootQuantity, setLootQuantity] = useState("1");
  const requestVersion = useRef(0);

  const refresh = useCallback(async (silent = false) => {
    const version = ++requestVersion.current;
    const requested = { generation, scopeKey };
    if (!enabled) {
      setView(null);
      return;
    }
    if (!silent) setError("");
    try {
      const next = await getInventory(props.campaignId);
      const current = trackerRef.current;
      if (
        version === requestVersion.current
        && current.key === requested.scopeKey
        && current.generation === requested.generation
      ) {
        setView(next);
      }
    } catch (cause) {
      const current = trackerRef.current;
      if (current.key === requested.scopeKey && current.generation === requested.generation) {
        setError(cause instanceof Error ? cause.message : "物品账本暂时不可用");
      }
    }
  }, [enabled, generation, props.campaignId, scopeKey, trackerRef]);

  const act = useCallback(async (key: string, operation: () => Promise<unknown>) => {
    setBusyId(key);
    setError("");
    try {
      await operation();
      await refresh(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "物品操作失败");
    } finally {
      setBusyId("");
    }
  }, [refresh]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(() => void refresh(true), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [enabled, refresh]);
  useEffect(() => { if (props.refreshKey) void refresh(true); }, [props.refreshKey, refresh]);

  const ownId = view?.self_investigator_id;
  return (
    <section className="tool-panel inventory-panel" aria-label="物品与资产">
      <div className="panel-heading">
        <div><p className="eyebrow">权威账本</p><h2>物品与资产</h2></div>
        <button aria-label="刷新物品账本" className="icon-button" onClick={() => void refresh()} type="button">
          <RefreshCw size={17} />
        </button>
      </div>
      <p className="panel-hint">持有、数量、装备、消耗和货币由规则内核提交；叙事不会直接改账。</p>
      {error && <p className="error-text" role="alert">{error}</p>}
      {!view ? <p className="empty-note">正在读取权威物品状态…</p> : (
        <>
          {props.identity.role === "kp" && (
            <details className="inventory-create-loot">
              <summary>登记公开战利品</summary>
              <div className="gameplay-command-grid">
                <label>名称<input aria-label="战利品名称" onChange={(event) => setLootName(event.target.value)} value={lootName} /></label>
                <label>数量<input aria-label="战利品数量" min={1} onChange={(event) => setLootQuantity(event.target.value)} type="number" value={lootQuantity} /></label>
                <label className="wide">公开说明<textarea aria-label="战利品公开说明" onChange={(event) => setLootDescription(event.target.value)} value={lootDescription} /></label>
              </div>
              <button
                className="primary-button"
                disabled={busyId === "create-loot" || !lootName.trim() || !Number.isInteger(Number(lootQuantity)) || Number(lootQuantity) < 1}
                onClick={() => void act("create-loot", async () => {
                  await createInventoryItem(props.campaignId, {
                    command_id: commandId("create-loot"),
                    item_type: "loot",
                    public_name: lootName.trim(),
                    public_description: lootDescription.trim(),
                    publicly_listed: true,
                    quantity: Number(lootQuantity),
                    is_unique: Number(lootQuantity) === 1,
                    holder_kind: "loot",
                    holder_id: `campaign:${props.campaignId}:public-loot`,
                    source_refs: [{ kind: "kp_ui", id: props.campaignId }],
                    reason: "KP 通过公开账本登记可拾取战利品"
                  });
                  setLootName("");
                  setLootDescription("");
                  setLootQuantity("1");
                })}
                type="button"
              >登记到权威账本</button>
            </details>
          )}
          <div className="inventory-balances">
            {view.balances.map((account) => (
              <span key={`${account.account_kind}:${account.account_id}:${account.currency_code}`}>
                <small>{account.account_kind === "party" ? "队伍资金" : "个人资金"}</small>
                <strong>{account.balance_minor} {account.currency_code}</strong>
              </span>
            ))}
          </div>
          <div className="inventory-grid">
            {view.items.map((item) => {
              const owned = item.holder_kind === "investigator" && item.holder_id === ownId;
              const canPickUp = props.identity.role === "player"
                && ["party", "location", "loot"].includes(item.holder_kind);
              const purchaseAccount = view.balances.find((account) =>
                account.account_kind === "investigator"
                && account.account_id === ownId
                && account.currency_code === item.currency_code
              );
              const canPurchase = props.identity.role === "player"
                && item.holder_kind === "npc"
                && item.publicly_listed
                && Boolean(ownId)
                && Boolean(item.currency_code)
                && Number(item.unit_value_minor) > 0;
              return (
                <article className="inventory-card" key={item.id}>
                  <div><strong>{item.public_name}</strong><small>×{item.quantity} · {item.holder_kind}</small></div>
                  {item.public_description && <p>{item.public_description}</p>}
                  {item.revealed_properties && <p className="inventory-revealed">已识别：{JSON.stringify(item.revealed_properties)}</p>}
                  {props.identity.role === "player" && (
                    <div className="compact-actions">
                      {canPickUp && <button disabled={busyId === item.id} onClick={() => void act(item.id, () => commandInventoryItem(item.id, { command_id: commandId("pickup"), command_type: "pickup", expected_version: item.version }))} type="button">拾取</button>}
                      {owned && !item.equipped_slot && <button disabled={busyId === item.id} onClick={() => void act(item.id, () => commandInventoryItem(item.id, { command_id: commandId("equip"), command_type: "equip", expected_version: item.version, equipped_slot: "carried" }))} type="button">装备</button>}
                      {owned && item.equipped_slot && <button disabled={busyId === item.id} onClick={() => void act(item.id, () => commandInventoryItem(item.id, { command_id: commandId("unequip"), command_type: "unequip", expected_version: item.version }))} type="button">卸下</button>}
                      {owned && item.item_type === "consumable" && <button disabled={busyId === item.id} onClick={() => void act(item.id, () => commandInventoryItem(item.id, { command_id: commandId("consume"), command_type: "consume", expected_version: item.version, quantity: 1 }))} type="button">消耗 1</button>}
                      {canPurchase && <button disabled={busyId === item.id || !purchaseAccount || purchaseAccount.balance_minor < Number(item.unit_value_minor)} onClick={() => void act(item.id, () => tradeInventoryItem(props.campaignId, { command_id: commandId("purchase"), direction: "purchase", item_id: item.id, expected_item_version: item.version, quantity: 1, investigator_id: ownId!, counterparty_kind: "vendor", counterparty_id: item.holder_id, currency_code: item.currency_code!, expected_investigator_balance_version: purchaseAccount?.version ?? null, expected_counterparty_balance_version: null }))} type="button">购买 {item.unit_value_minor} {item.currency_code}</button>}
                    </div>
                  )}
                  {owned && view.transfer_targets.length > 0 && (
                    <div className="inventory-offer-row">
                      <select aria-label={`将${item.public_name}交给`} onChange={(event) => setTargets((current) => ({ ...current, [item.id]: event.target.value }))} value={targets[item.id] ?? ""}>
                        <option value="">选择接收者</option>
                        {view.transfer_targets.map((target) => <option key={target.investigator_id} value={target.investigator_id}>{target.name}</option>)}
                      </select>
                      <button disabled={!targets[item.id] || busyId === item.id} onClick={() => void act(item.id, () => createInventoryOffer(props.campaignId, { command_id: commandId("offer"), item_id: item.id, expected_item_version: item.version, quantity: 1, to_investigator_id: targets[item.id] }))} type="button">提出转交</button>
                    </div>
                  )}
                </article>
              );
            })}
            {!view.items.length && <p className="empty-note"><PackageOpen size={18} /> 当前没有可见物品。</p>}
          </div>
          {view.transfer_offers.length > 0 && (
            <div className="inventory-offers">
              <h3>待处理转交</h3>
              {view.transfer_offers.map((offer) => (
                <article key={offer.id}>
                  <span>数量 {offer.quantity} · {offer.from_investigator_id} → {offer.to_investigator_id}</span>
                  <div className="compact-actions">
                    {offer.to_investigator_id === ownId && <>
                      <button onClick={() => void act(offer.id, () => decideInventoryOffer(offer.id, { command_id: commandId("accept"), expected_version: offer.version, decision: "accept" }))} type="button">接受</button>
                      <button onClick={() => void act(offer.id, () => decideInventoryOffer(offer.id, { command_id: commandId("decline"), expected_version: offer.version, decision: "decline" }))} type="button">拒绝</button>
                    </>}
                    {offer.from_investigator_id === ownId && <button onClick={() => void act(offer.id, () => decideInventoryOffer(offer.id, { command_id: commandId("cancel"), expected_version: offer.version, decision: "cancel" }))} type="button">撤回</button>}
                  </div>
                </article>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}

export function inventoryItemLabel(item: InventoryItem) {
  return `${item.public_name} ×${item.quantity}`;
}
