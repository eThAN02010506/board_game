import { Check, Plus } from "lucide-react";
import type { FormEventHandler } from "react";
import type { Campaign, Role } from "../../api/types";

type Props = {
  role: Role | undefined;
  adminToken: string;
  campaignTitle: string;
  campaignTime: string;
  campaigns: Campaign[];
  activeCampaignId?: string;
  onAdminTokenChange: (value: string) => void;
  onCampaignTitleChange: (value: string) => void;
  onCampaignTimeChange: (value: string) => void;
  onApplyAdminToken: () => void;
  onCreateCampaign: FormEventHandler<HTMLFormElement>;
  onSelectCampaign: (campaign: Campaign) => void;
};

export function CampaignPanel(props: Props) {
  return (
    <section className="tool-panel campaign-panel" id="workspace-section">
      <div className="panel-heading">
        <h2>团与时间</h2>
        <Check size={18} />
      </div>
      {props.role !== "player" && props.role !== "observer" && (
        <form onSubmit={props.onCreateCampaign}>
          <label>
            管理员口令（LAN/HTTPS 部署时使用）
            <input
              autoComplete="off"
              type="password"
              value={props.adminToken}
              onChange={(event) => props.onAdminTokenChange(event.target.value)}
            />
          </label>
          <button className="ghost-button" onClick={props.onApplyAdminToken} type="button">
            应用/清除管理员口令
          </button>
          <label>
            团名
            <input
              value={props.campaignTitle}
              onChange={(event) => props.onCampaignTitleChange(event.target.value)}
            />
          </label>
          <label>
            当前时间
            <input
              value={props.campaignTime}
              onChange={(event) => props.onCampaignTimeChange(event.target.value)}
            />
          </label>
          <button className="primary-button" type="submit">
            <Plus size={16} />
            创建 Campaign
          </button>
        </form>
      )}
      <div className="list-stack">
        {props.campaigns.map((campaign) => (
          <button
            className={`record-button ${campaign.id === props.activeCampaignId ? "selected" : ""}`}
            key={campaign.id}
            onClick={() => props.onSelectCampaign(campaign)}
            type="button"
          >
            <span>{campaign.title}</span>
            <small>{campaign.current_time ?? campaign.system}</small>
          </button>
        ))}
      </div>
    </section>
  );
}
