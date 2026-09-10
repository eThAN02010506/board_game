"""Deterministic playable-source ranges for single scenarios and compendiums."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

_TOP_LEVEL_SCOPE = re.compile(
    r"^(?:"
    r"chapter\s+[0-9ivxlcdm]+"
    r"|(?:adventure|scenario|module)\s+[0-9ivxlcdm]+"
    r"|第[一二三四五六七八九十百0-9]+[章节幕篇部]"
    r")\s*[:：.．、-]?\s*\S+",
    re.IGNORECASE,
)
_MIN_TOC_BOUNDARY_RUN = 3


@dataclass(frozen=True)
class ScenarioSourceScope:
    key: str
    title: str
    start_order_index: int
    end_order_index: int
    block_count: int
    character_count: int
    first_page: int | None
    last_page: int | None
    semantic_counts: dict[str, int]
    whole_document: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "start_order_index": self.start_order_index,
            "end_order_index": self.end_order_index,
            "block_count": self.block_count,
            "character_count": self.character_count,
            "first_page": self.first_page,
            "last_page": self.last_page,
            "semantic_counts": dict(self.semantic_counts),
            "whole_document": self.whole_document,
        }


def infer_scenario_source_scopes(
    module_title: str,
    chunks: list[dict[str, Any]],
) -> tuple[ScenarioSourceScope, ...]:
    """Expose stable ranges without guessing which chapter is a scenario.

    An explicit numbered chapter/adventure boundary is strong enough to split a
    compendium. Scene headings are intentionally excluded because they are
    normally internal to one playable scenario. The caller, not this heuristic,
    decides which range is playable.
    """

    ordered = sorted(
        chunks,
        key=lambda item: (int(item.get("order_index") or 0), str(item.get("id") or "")),
    )
    if not ordered:
        return ()
    whole = _scope(
        "whole-document",
        module_title,
        ordered,
        whole_document=True,
    )
    boundary_candidates = [
        index
        for index, chunk in enumerate(ordered)
        if str(chunk.get("semantic_kind") or "") == "heading"
        and _TOP_LEVEL_SCOPE.match(_normalized_heading(chunk))
    ]
    toc_boundaries = _toc_boundary_positions(ordered, boundary_candidates)
    boundaries = [index for index in boundary_candidates if index not in toc_boundaries]
    if len(boundaries) < 2:
        return (whole,)

    scopes = [whole]
    for boundary_offset, start in enumerate(boundaries):
        stop = (
            boundaries[boundary_offset + 1]
            if boundary_offset + 1 < len(boundaries)
            else len(ordered)
        )
        scoped_chunks = ordered[start:stop]
        title = _normalized_heading(scoped_chunks[0])
        scopes.append(
            _scope(
                _scope_key(title, int(scoped_chunks[0].get("order_index") or 0)),
                title,
                scoped_chunks,
            )
        )
    return tuple(scopes)


def chunks_for_scenario_scope(
    chunks: list[dict[str, Any]],
    scope: ScenarioSourceScope,
) -> list[dict[str, Any]]:
    return [
        chunk
        for chunk in chunks
        if scope.start_order_index <= int(chunk.get("order_index") or 0) <= scope.end_order_index
    ]


def _scope(
    key: str,
    title: str,
    chunks: list[dict[str, Any]],
    *,
    whole_document: bool = False,
) -> ScenarioSourceScope:
    semantic_counts: dict[str, int] = {}
    pages = []
    for chunk in chunks:
        kind = str(chunk.get("semantic_kind") or "text")
        semantic_counts[kind] = semantic_counts.get(kind, 0) + 1
        for field in ("page_start", "page_end"):
            value = chunk.get(field)
            if value is not None:
                pages.append(int(value))
    return ScenarioSourceScope(
        key=key,
        title=title.strip(),
        start_order_index=int(chunks[0].get("order_index") or 0),
        end_order_index=int(chunks[-1].get("order_index") or 0),
        block_count=len(chunks),
        character_count=sum(len(str(item.get("text") or "")) for item in chunks),
        first_page=min(pages) if pages else None,
        last_page=max(pages) if pages else None,
        semantic_counts=semantic_counts,
        whole_document=whole_document,
    )


def _normalized_heading(chunk: dict[str, Any]) -> str:
    return " ".join(str(chunk.get("text") or chunk.get("title") or "").split()).strip()


def _toc_boundary_positions(
    ordered: list[dict[str, Any]],
    boundary_candidates: list[int],
) -> set[int]:
    """Find dense same-page chapter lists without suppressing real boundaries.

    A table of contents commonly produces several adjacent heading chunks on one
    page. Two adjacent headings can still be legitimate (for example, a short
    front-matter chapter followed by the next chapter), so only runs of three or
    more candidates are ignored. Any intervening non-boundary chunk breaks the
    run and preserves the headings.
    """

    toc_boundaries: set[int] = set()
    run: list[int] = []
    run_page: int | None = None

    def finish_run() -> None:
        if len(run) >= _MIN_TOC_BOUNDARY_RUN:
            toc_boundaries.update(run)

    for position in boundary_candidates:
        page = _single_page(ordered[position])
        continues_run = bool(run) and page is not None and page == run_page and position == run[-1] + 1
        if not continues_run:
            finish_run()
            run = []
            run_page = page
        if page is not None:
            run.append(position)

    finish_run()
    return toc_boundaries


def _single_page(chunk: dict[str, Any]) -> int | None:
    start = chunk.get("page_start")
    end = chunk.get("page_end")
    if start is None or end is None:
        return None
    try:
        start_page = int(start)
        end_page = int(end)
    except (TypeError, ValueError):
        return None
    return start_page if start_page == end_page else None


def _scope_key(title: str, start_order_index: int) -> str:
    digest = hashlib.sha256(f"{start_order_index}\0{title}".encode()).hexdigest()[:12]
    return f"section-{start_order_index}-{digest}"


__all__ = [
    "ScenarioSourceScope",
    "chunks_for_scenario_scope",
    "infer_scenario_source_scopes",
]
