import i18n from "../i18n";

export function statusLabel(status: string | null | undefined): string {
  if (!status) return i18n.t("common.unknown");
  const key = `status.${status}`;
  const translated = i18n.t(key);
  // 没有对应词条时回退原文，避免吞掉未知状态。
  return translated !== key ? translated : status;
}
