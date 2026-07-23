"""Application policy for player-visible KP character review decisions."""


def validate_review_decision(action: str, comment: str | None) -> str | None:
    if action not in {"approved", "changes_requested"}:
        raise ValueError("Unsupported investigator review action")
    normalized_comment = (comment or "").strip()
    if action == "changes_requested" and not normalized_comment:
        raise ValueError("A change request must include a player-visible comment")
    return normalized_comment or None
