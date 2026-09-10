"""Architecture lint: named acceptance scenarios must not leak into product runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOTS = (PROJECT_ROOT / "src" / "ai_kp", PROJECT_ROOT / "apps" / "web" / "src")
EXCLUDED_PARTS = {"rulesets", "__pycache__"}

# These belong to named replay fixtures or scenario documents, never product decisions,
# prompts, comments, or UI defaults. Ruleset aliases are excluded above because they are
# language vocabulary, not scenario authority.
SCENARIO_FIXTURE_TERMS = (
    "常暗之厢",
    "长暗之厢",
    "万能钥匙",
    "驾驶室钥匙",
    "巨大口器",
    "京山 人吉",
    "失散亲属",
)


def product_text_files() -> tuple[Path, ...]:
    return tuple(
        path
        for root in PRODUCT_ROOTS
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".ts", ".tsx"}
        and not EXCLUDED_PARTS.intersection(path.parts)
        and ".test." not in path.name
    )


@pytest.mark.parametrize("path", product_text_files(), ids=lambda path: str(path))
def test_named_scenario_terms_do_not_enter_product_runtime(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    found = [term for term in SCENARIO_FIXTURE_TERMS if term in text]
    assert found == [], f"scenario fixture vocabulary leaked into runtime: {found}"
