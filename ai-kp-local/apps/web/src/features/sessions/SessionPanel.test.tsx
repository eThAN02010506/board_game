import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  AuthIdentity,
  SessionInfo,
  SessionMember,
  SessionSeat
} from "../../api/types";
import { SessionPanel } from "./SessionPanel";

const session: SessionInfo = {
  id: "session_test",
  campaign_id: "campaign_test",
  title: "雾港第三夜",
  status: "active"
};

const kpIdentity: AuthIdentity = {
  member_id: "member_kp",
  session_id: session.id,
  campaign_id: session.campaign_id,
  role: "kp",
  display_name: "守密人",
  pc_id: null
};

const playerIdentity: AuthIdentity = {
  member_id: "member_player",
  session_id: session.id,
  campaign_id: session.campaign_id,
  role: "player",
  display_name: "林若川",
  pc_id: "pc_luo",
  seat_id: "seat_claimed"
};

const seats: SessionSeat[] = [
  {
    id: "seat_open",
    session_id: session.id,
    campaign_id: session.campaign_id,
    label: "玩家一席",
    status: "open",
    assigned_pc_id: null,
    player_profile_id: null,
    claimed_member_id: null,
    profile_display_name: null,
    member_display_name: null,
    pc_name: null,
    has_active_invitation: true
  },
  {
    id: "seat_claimed",
    session_id: session.id,
    campaign_id: session.campaign_id,
    label: "林若川席",
    status: "claimed",
    assigned_pc_id: "pc_luo",
    player_profile_id: "profile_player",
    claimed_member_id: "member_player",
    profile_display_name: "林玩家",
    member_display_name: "林玩家",
    pc_name: "林若川",
    has_active_invitation: false
  }
];

const members: SessionMember[] = [
  {
    id: "member_player",
    session_id: session.id,
    campaign_id: session.campaign_id,
    role: "player",
    display_name: "林玩家",
    pc_id: "pc_luo",
    revoked_at: null
  }
];

function renderPanel({
  identity = null,
  activeCampaignPresent = true,
  recoverableSeats = []
}: {
  identity?: AuthIdentity | null;
  activeCampaignPresent?: boolean;
  recoverableSeats?: SessionSeat[];
} = {}) {
  const callbacks = {
    onKpDisplayNameChange: vi.fn(),
    onJoinCodeInputChange: vi.fn(),
    onSeatInvitationInputChange: vi.fn(),
    onSeatLabelChange: vi.fn(),
    onPlayerDisplayNameChange: vi.fn(),
    onRefreshIdentity: vi.fn(),
    onSwitchIdentity: vi.fn(),
    onRotateJoinCode: vi.fn(),
    onRefreshMembers: vi.fn(),
    onRefreshSeats: vi.fn(),
    onRefreshRecoverableSeats: vi.fn(),
    onCloseSession: vi.fn(),
    onCreateSeat: vi.fn((event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
    }),
    onClaimSeat: vi.fn((event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
    }),
    onReissueSeat: vi.fn(),
    onRevokeSeat: vi.fn(),
    onRecoverSeat: vi.fn(),
    onRevokeMember: vi.fn(),
    onStartSession: vi.fn(),
    onRecoverKp: vi.fn(),
    onOpenInvestigators: vi.fn(),
    onJoinSession: vi.fn((event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
    })
  };

  const view = render(
    <SessionPanel
      activeCampaignPresent={activeCampaignPresent}
      identity={identity}
      joinRole="player"
      joinCodeInput=""
      kpDisplayName="守密人"
      members={members}
      playerDisplayName=""
      realtimeNote="实时连接正常"
      realtimeStatus="live"
      recoverableSeats={recoverableSeats}
      seatInvitationInput=""
      seatLabel=""
      seats={seats}
      session={identity ? session : null}
      visibleJoinCode=""
      visibleSeatInvites={{ seat_open: "SEAT-OPEN-CODE" }}
      onJoinRoleChange={vi.fn()}
      {...callbacks}
    />
  );

  return { ...view, ...callbacks };
}

describe("SessionPanel", () => {
  it("lets an authenticated member return to identity selection", () => {
    const { onSwitchIdentity } = renderPanel({ identity: kpIdentity });

    fireEvent.click(screen.getByRole("button", { name: "切换身份" }));

    expect(onSwitchIdentity).toHaveBeenCalledTimes(1);
  });

  it("lets a visitor claim a seat and only recover active claimed seats", () => {
    const recoverableSeats: SessionSeat[] = [
      {
        ...seats[1],
        campaign_title: "雾港疑云",
        session_title: "雾港第三夜",
        session_status: "active"
      },
      {
        ...seats[1],
        id: "seat_closed",
        campaign_title: "旧案",
        session_title: "终幕",
        session_status: "closed"
      },
      {
        ...seats[0],
        id: "seat_unclaimed",
        campaign_title: "未开始的团",
        session_title: "准备阶段",
        session_status: "active"
      }
    ];
    const {
      onClaimSeat,
      onPlayerDisplayNameChange,
      onRecoverSeat,
      onSeatInvitationInputChange
    } = renderPanel({ recoverableSeats });

    fireEvent.change(screen.getByLabelText("席位邀请码"), {
      target: { value: "MIST-HARBOR-01" }
    });
    fireEvent.change(screen.getByLabelText("玩家显示名"), {
      target: { value: "林玩家" }
    });
    fireEvent.click(screen.getByRole("button", { name: "认领并进入席位" }));

    expect(onSeatInvitationInputChange).toHaveBeenCalledWith("MIST-HARBOR-01");
    expect(onPlayerDisplayNameChange).toHaveBeenCalledWith("林玩家");
    expect(onClaimSeat).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /雾港疑云.*恢复进入/ })).toBeVisible();
    expect(screen.queryByText("旧案")).not.toBeInTheDocument();
    expect(screen.queryByText("未开始的团")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /雾港疑云.*恢复进入/ }));
    expect(onRecoverSeat).toHaveBeenCalledWith("seat_claimed");
  });

  it("disables starting a KP session until a campaign is selected", () => {
    const { onRecoverKp, onStartSession } = renderPanel({ activeCampaignPresent: false });

    const startButton = screen.getByRole("button", { name: "为当前团开启 KP 会话" });
    const recoverButton = screen.getByRole("button", { name: "重签并恢复当前团 KP" });
    expect(startButton).toBeDisabled();
    expect(recoverButton).toBeDisabled();
    fireEvent.click(startButton);
    fireEvent.click(recoverButton);
    expect(onStartSession).not.toHaveBeenCalled();
    expect(onRecoverKp).not.toHaveBeenCalled();
  });

  it("allows a local administrator to request KP credential recovery", () => {
    const { onRecoverKp } = renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "重签并恢复当前团 KP" }));

    expect(onRecoverKp).toHaveBeenCalledTimes(1);
  });

  it("gives a KP seat creation, investigator-flow guidance, reissue, and revocation controls", () => {
    const {
      onCreateSeat,
      onOpenInvestigators,
      onRefreshSeats,
      onReissueSeat,
      onRevokeSeat,
      onSeatLabelChange
    } = renderPanel({ identity: kpIdentity });

    fireEvent.change(screen.getByLabelText("席位名称"), {
      target: { value: "调查员二席" }
    });
    fireEvent.click(screen.getByRole("button", { name: "创建单席邀请" }));

    expect(onSeatLabelChange).toHaveBeenCalledWith("调查员二席");
    expect(onCreateSeat).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/尚未绑定调查员；请在角色卡页完成审批与绑定/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "前往角色卡审核与绑定" }));
    expect(onOpenInvestigators).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "重新签发" }));
    expect(onReissueSeat).toHaveBeenCalledWith("seat_open");
    fireEvent.click(screen.getAllByRole("button", { name: "撤销此席" })[0]);
    expect(onRevokeSeat).toHaveBeenCalledWith("seat_open");
    fireEvent.click(screen.getByTitle("刷新席位"));
    expect(onRefreshSeats).toHaveBeenCalledTimes(1);
  });

  it("shows a player's stable identity without KP seat administration", () => {
    const { onRefreshIdentity } = renderPanel({ identity: playerIdentity });

    expect(screen.getByText("玩家", { selector: ".role-badge" })).toBeVisible();
    expect(screen.getByText(/稳定席位 seat_claimed/)).toBeVisible();
    expect(screen.queryByText("玩家席位")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "创建单席邀请" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "刷新身份与角色绑定" }));
    expect(onRefreshIdentity).toHaveBeenCalledTimes(1);
  });
});
