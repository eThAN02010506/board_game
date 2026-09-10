"""Audit heterogeneous scenario documents with the production parser.

The report is intentionally content-agnostic: it measures document structure,
playable source ranges, and deterministic contract obligations without relying
on scenario titles or keywords tied to a particular adventure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from ai_kp.infrastructure.modules.document_sandbox import (
    DocumentParsePolicy,
    extract_module_document_isolated,
)
from ai_kp.platform.modules.documents import extract_module_document
from ai_kp.platform.modules.scenario_scopes import infer_scenario_source_scopes
from ai_kp.platform.resolution.source_coverage import (
    infer_source_coverage_requirements,
)


def audit_document(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    if path.suffix.lower() == ".doc":
        # The isolated parser intentionally creates its private process workspace
        # beside the source. Copy read-only uploads to a disposable writable root.
        with tempfile.TemporaryDirectory(prefix="module-corpus-audit-") as temp_root:
            parse_source = Path(temp_root) / path.name
            shutil.copyfile(path, parse_source)
            extracted = extract_module_document_isolated(
                parse_source,
                path.name,
                title=path.stem,
                expected_hash=hashlib.sha256(data).hexdigest(),
                policy=DocumentParsePolicy(),
            )
    else:
        extracted = extract_module_document(data, path.name, title=path.stem)

    chunks = [
        asdict(chunk) | {"id": f"block-{index}"}
        for index, chunk in enumerate(extracted.chunks)
    ]
    scopes = infer_scenario_source_scopes(extracted.title, chunks)
    semantic_counts = Counter(chunk.semantic_kind for chunk in extracted.chunks)
    obligation_counts: Counter[str] = Counter()
    for chunk in extracted.chunks:
        for requirement in infer_source_coverage_requirements(
            semantic_kind=chunk.semantic_kind,
            classification_confidence=chunk.classification_confidence,
            text=chunk.text,
        ):
            obligation_counts[requirement.requirement_key] += requirement.minimum_record_count

    return {
        "path": str(path),
        "source_type": extracted.source_type,
        "units": extracted.unit_count,
        "chunks": len(extracted.chunks),
        "assets": len(extracted.assets),
        "semantic_counts": dict(sorted(semantic_counts.items())),
        "contract_obligations": dict(sorted(obligation_counts.items())),
        "source_scopes": [scope.as_dict() for scope in scopes],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("documents", nargs="+", type=Path)
    args = parser.parse_args()
    report = [audit_document(path.resolve()) for path in args.documents]
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
