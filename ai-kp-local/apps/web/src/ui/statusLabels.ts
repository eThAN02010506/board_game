const STATUS_LABELS: Record<string, string> = {
  active: "进行中",
  closed: "已关闭",
  draft: "草稿",
  pending: "等待处理",
  submitted: "已提交",
  reviewed: "已复核",
  approved: "已批准",
  rejected: "已拒绝",
  requested: "等待检定",
  resolved: "已结算",
  overridden: "KP 已裁定",
  cancelled: "已取消",
  open: "等待认领",
  claimed: "已认领",
  revoked: "已撤销",
  failed: "失败",
  ready: "已就绪",
  processing: "处理中",
  queued: "排队中",
  validating: "校验中",
  extracting: "提取中",
  extracted: "已提取",
  indexing: "索引中",
  storing: "保存中",
  completed: "已完成",
  pending_analysis: "等待分析",
  candidate: "待审核候选",
  validated: "已验证",
  review_required: "需要复核",
  quarantined: "已隔离"
};

export function statusLabel(status: string | null | undefined): string {
  if (!status) return "未知";
  return STATUS_LABELS[status] ?? status;
}
