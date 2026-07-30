"""Derive safe prior-NPC choices from stable investigator encounter history."""

from __future__ import annotations

import re
import unicodedata

from ai_kp.application.ports.npc_reappearances import NpcReappearanceStore
from ai_kp.application.travel_graph_service import TravelGraphService


class NpcReappearanceService:
    def __init__(self, repo: NpcReappearanceStore):
        self.repo = repo

    def list_candidates(
        self,
        campaign_id: str,
        *,
        query: str | None = None,
        context_location: str | None = None,
        profession_hint: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        normalized_query = self._normalize(query or "")
        campaign = self.repo.get_campaign(campaign_id)
        policy = self.repo.get_campaign_npc_reappearance_policy(campaign_id)
        used_budget = self.repo.count_npc_reappearances(campaign_id)
        remaining_budget = max(
            0,
            int(policy["max_returning_npcs"]) - used_budget,
        )
        if remaining_budget == 0:
            return []
        grouped: dict[str, dict] = {}
        for row in self.repo.list_npc_reappearance_candidate_rows(campaign_id):
            searchable = self._normalize(
                " ".join(
                    str(row.get(key) or "")
                    for key in (
                        "name",
                        "profession",
                        "home_location",
                        "investigator_name",
                        "interaction_summary",
                    )
                )
            )
            if normalized_query and normalized_query not in searchable:
                continue
            npc_id = str(row["npc_id"])
            candidate = grouped.setdefault(
                npc_id,
                {
                    "npc_id": npc_id,
                    "name": str(row["name"]),
                    "profession": row.get("profession"),
                    "home_location": row.get("home_location"),
                    "qualifying_investigators": [],
                },
            )
            evidence_key = (
                str(row["investigator_id"]),
                str(row["interaction_summary"]),
                str(row.get("happened_at") or ""),
            )
            existing_keys = {
                (
                    item["investigator_id"],
                    item["interaction_summary"],
                    item["happened_at"] or "",
                )
                for item in candidate["qualifying_investigators"]
            }
            if (
                evidence_key not in existing_keys
                and len(candidate["qualifying_investigators"]) < 8
            ):
                candidate["qualifying_investigators"].append(
                    {
                        "investigator_id": str(row["investigator_id"]),
                        "investigator_name": str(row["investigator_name"]),
                        "interaction_summary": str(row["interaction_summary"]),
                        "happened_at": row.get("happened_at"),
                    }
                )
        candidates = []
        for candidate in grouped.values():
            profile = self.repo.get_npc_availability_profile(candidate["npc_id"])
            decision = self.evaluate(
                campaign_time=campaign.get("current_time"),
                policy=policy,
                profile=profile,
                context_location=context_location,
                profession_hint=profession_hint,
                travel_result=self.travel_result(
                    campaign_id,
                    profile,
                    context_location,
                    int(policy["max_travel_minutes"]),
                ),
            )
            if decision["decision"] == "blocked":
                continue
            candidate["appearance_gate"] = {
                **decision,
                "remaining_campaign_budget": remaining_budget,
                "max_returning_npcs": int(policy["max_returning_npcs"]),
            }
            candidate["availability_profile"] = (
                {
                    key: profile.get(key)
                    for key in (
                        "lifecycle_state",
                        "born_year",
                        "died_year",
                        "active_from_year",
                        "active_until_year",
                        "location_tags",
                        "profession_tags",
                    )
                }
                if profile is not None
                else None
            )
            candidates.append(candidate)
        candidates.sort(key=lambda item: (item["name"].casefold(), item["npc_id"]))
        return candidates[:limit]

    def list_campaign_npcs(self, campaign_id: str) -> list[dict]:
        return self.repo.list_campaign_npcs_with_profiles(campaign_id)

    def get_campaign_policy(self, campaign_id: str) -> dict:
        return self.repo.get_campaign_npc_reappearance_policy(campaign_id)

    def save_availability_profile(
        self,
        campaign_id: str,
        npc_id: str,
        values: dict,
    ) -> dict:
        if not self.repo.npc_is_linked_to_campaign(campaign_id, npc_id):
            raise KeyError(f"Campaign NPC not found: {npc_id}")
        normalized = dict(values)
        for key in ("location_tags", "profession_tags"):
            normalized[key] = list(
                dict.fromkeys(
                    self._normalize(item)
                    for item in values.get(key, [])
                    if self._normalize(item)
                )
            )
        return self.repo.save_npc_availability_profile(npc_id, **normalized)

    def save_campaign_policy(self, campaign_id: str, values: dict) -> dict:
        return self.repo.save_campaign_npc_reappearance_policy(
            campaign_id,
            **values,
        )

    @classmethod
    def evaluate(
        cls,
        *,
        campaign_time: str | None,
        policy: dict,
        profile: dict | None,
        context_location: str | None,
        profession_hint: str | None,
        travel_result: dict | None = None,
        final: bool = False,
    ) -> dict:
        reasons: list[str] = ["存在当前已批准调查员的跨团接触记录"]
        warnings: list[str] = []
        if profile is None:
            warnings.append("尚未配置 NPC 可用性档案，需要 KP 复核")
            return {
                "decision": "needs_review",
                "reasons": reasons,
                "warnings": warnings,
            }

        state = str(profile.get("lifecycle_state") or "unknown")
        if state == "unavailable":
            return cls._blocked("NPC 当前被明确标记为不可出场")
        if state in {"unknown", "missing"}:
            warnings.append(
                "NPC 状态未知，需要 KP 复核"
                if state == "unknown"
                else "NPC 当前失踪，出场需要 KP 解释"
            )

        year = cls._campaign_year(campaign_time)
        bounded = any(
            profile.get(key) is not None
            for key in ("born_year", "died_year", "active_from_year", "active_until_year")
        )
        if year is None and bounded:
            warnings.append("团时间没有可解析的四位年份，无法校验 NPC 年代")
        elif year is not None:
            if profile.get("born_year") is not None and year < int(profile["born_year"]):
                return cls._blocked(f"团时间 {year} 早于 NPC 出生年份")
            if profile.get("died_year") is not None and year > int(profile["died_year"]):
                return cls._blocked(f"团时间 {year} 晚于 NPC 死亡年份")
            if (
                profile.get("active_from_year") is not None
                and year < int(profile["active_from_year"])
            ):
                return cls._blocked(f"团时间 {year} 早于 NPC 活动年代")
            if (
                profile.get("active_until_year") is not None
                and year > int(profile["active_until_year"])
            ):
                return cls._blocked(f"团时间 {year} 晚于 NPC 活动年代")
            if bounded:
                reasons.append(f"NPC 年代范围与团时间 {year} 相容")

        blocked_reason = cls._evaluate_location(
            supplied=context_location,
            allowed=profile.get("location_tags") or [],
            strict=bool(policy["require_location_match"]),
            final=final,
            travel_result=travel_result,
            reasons=reasons,
            warnings=warnings,
        )
        if blocked_reason:
            return cls._blocked(blocked_reason)
        blocked_reason = cls._evaluate_tag_constraint(
            label="职业",
            supplied=profession_hint,
            allowed=profile.get("profession_tags") or [],
            strict=bool(policy["require_profession_match"]),
            final=final,
            reasons=reasons,
            warnings=warnings,
        )
        if blocked_reason:
            return cls._blocked(blocked_reason)
        return {
            "decision": "needs_review" if warnings else "eligible",
            "reasons": reasons,
            "warnings": warnings,
        }

    def travel_result(
        self,
        campaign_id: str,
        profile: dict | None,
        context_location: str | None,
        max_minutes: int,
    ) -> dict | None:
        if profile is None or not context_location or not profile.get("location_tags"):
            return None
        return TravelGraphService(self.repo).preview(
            campaign_id,
            origins=tuple(profile["location_tags"]),
            destination=context_location,
            max_minutes=max_minutes,
        )

    @classmethod
    def _evaluate_location(
        cls,
        *,
        supplied: str | None,
        allowed: list[str],
        strict: bool,
        final: bool,
        travel_result: dict | None,
        reasons: list[str],
        warnings: list[str],
    ) -> str | None:
        if travel_result is not None:
            status = travel_result["status"]
            if status in {"same_location", "reachable"}:
                minutes = int(travel_result["total_minutes"] or 0)
                route_names = " → ".join(
                    item["name"] for item in travel_result["locations"]
                )
                reasons.append(f"地点可达：{route_names}（{minutes} 分钟）")
                return None
            if status == "over_limit":
                message = (
                    f"最短旅行时间 {travel_result['total_minutes']} 分钟超过"
                    f"本团限制 {travel_result['max_minutes']} 分钟"
                )
                if strict:
                    return message
                warnings.append(message)
                return None
            if status == "unreachable":
                message = "地点图谱中不存在开放路线"
                if strict:
                    return message
                warnings.append(message)
                return None
            if status in {"unresolved_origin", "unresolved_destination"}:
                message = "地点名称无法解析到团级地点图谱"
                if strict and final:
                    return message
                warnings.append(message)
                return None
        return cls._evaluate_tag_constraint(
            label="地点",
            supplied=supplied,
            allowed=allowed,
            strict=strict,
            final=final,
            reasons=reasons,
            warnings=warnings,
        )

    @classmethod
    def _evaluate_tag_constraint(
        cls,
        *,
        label: str,
        supplied: str | None,
        allowed: list[str],
        strict: bool,
        final: bool,
        reasons: list[str],
        warnings: list[str],
    ) -> str | None:
        normalized_value = cls._normalize(supplied or "")
        normalized_allowed = [cls._normalize(item) for item in allowed if cls._normalize(item)]
        if not normalized_value:
            if strict and normalized_allowed:
                message = f"{label}上下文缺失，严格模式要求明确匹配"
                if final:
                    return message
                warnings.append(message)
            return None
        if not normalized_allowed:
            warnings.append(f"NPC 没有配置可校验的{label}标签")
            return None
        if any(
            item in normalized_value or normalized_value in item
            for item in normalized_allowed
        ):
            reasons.append(f"{label}“{supplied}”与 NPC 档案相容")
        else:
            if strict:
                return f"{label}不匹配，严格模式拒绝出场"
            warnings.append(f"{label}不匹配，需要 KP 解释")
        return None

    @staticmethod
    def _campaign_year(value: str | None) -> int | None:
        if not value:
            return None
        match = re.match(r"^\s*(\d{4})(?:\D|$)", value)
        return int(match.group(1)) if match else None

    @staticmethod
    def _blocked(reason: str) -> dict:
        return {"decision": "blocked", "reasons": [], "warnings": [reason]}

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


__all__ = ["NpcReappearanceService"]
