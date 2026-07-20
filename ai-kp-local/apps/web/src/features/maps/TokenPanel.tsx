import { Plus, Route, Send } from "lucide-react";
import type { FormEventHandler } from "react";
import type {
  AuthIdentity,
  MapToken,
  PlayerCharacter,
  SavedMap
} from "../../api/types";

type Props = {
  identity: AuthIdentity | null;
  activeMap: SavedMap | null;
  pcs: PlayerCharacter[];
  movableTokens: MapToken[];
  selectedToken: MapToken | null;
  selectedTokenId: string;
  tokenLabel: string;
  tokenActorId: string;
  tokenLocation: string;
  moveTarget: string;
  onSelectedTokenIdChange: (value: string) => void;
  onTokenLabelChange: (value: string) => void;
  onTokenActorIdChange: (value: string) => void;
  onTokenLocationChange: (value: string) => void;
  onMoveTargetChange: (value: string) => void;
  onPlaceToken: FormEventHandler<HTMLFormElement>;
  onMoveToken: FormEventHandler<HTMLFormElement>;
};

export function TokenPanel(props: Props) {
  return (
    <section className="tool-panel token-panel">
      <div className="panel-heading">
        <h2>棋子移动</h2>
        <Route size={18} />
      </div>
      {props.identity?.role === "kp" && (
        <form onSubmit={props.onPlaceToken}>
          <label>
            棋子名
            <input
              value={props.tokenLabel}
              onChange={(event) => props.onTokenLabelChange(event.target.value)}
            />
          </label>
          <label>
            绑定玩家角色
            <select
              value={props.tokenActorId}
              onChange={(event) => props.onTokenActorIdChange(event.target.value)}
            >
              <option value="">不绑定（玩家无法操作）</option>
              {props.pcs.map((pc) => (
                <option key={pc.id} value={pc.id}>
                  {pc.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            放置地点
            <select
              value={props.tokenLocation}
              onChange={(event) => props.onTokenLocationChange(event.target.value)}
            >
              {(props.activeMap?.locations ?? []).map((location) => (
                <option key={location.id} value={location.name}>
                  {location.name}
                </option>
              ))}
              {!props.activeMap && <option>旧码头</option>}
            </select>
          </label>
          <button className="secondary-button" type="submit">
            <Plus size={16} />
            放置棋子
          </button>
        </form>
      )}

      {props.identity ? (
        <form onSubmit={props.onMoveToken}>
          <label>
            选择棋子
            <select
              value={props.selectedTokenId}
              onChange={(event) => props.onSelectedTokenIdChange(event.target.value)}
            >
              <option value="">未选择</option>
              {props.movableTokens.map((token) => (
                <option key={token.id} value={token.id}>
                  {token.label} @ {token.location_name}
                </option>
              ))}
            </select>
          </label>
          <label>
            移动到
            <select
              value={props.moveTarget}
              onChange={(event) => props.onMoveTargetChange(event.target.value)}
            >
              {(props.activeMap?.locations ?? []).map((location) => (
                <option key={location.id} value={location.name}>
                  {location.name}
                </option>
              ))}
              {!props.activeMap && <option>废弃仓库</option>}
            </select>
          </label>
          <button className="primary-button" type="submit">
            <Send size={16} />
            移动
          </button>
        </form>
      ) : (
        <p className="permission-hint">加入团会话后才能操作棋子。</p>
      )}
      <div className="token-readout">
        {props.selectedToken
          ? `${props.selectedToken.label} 当前在 ${props.selectedToken.location_name}`
          : props.identity?.role === "player" && !props.identity.pc_id
            ? "待 KP 绑定角色卡，绑定后请刷新身份"
            : "未选择可操作棋子"}
      </div>
    </section>
  );
}
