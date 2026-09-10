"""One-off: deterministically attach character attribute chunks to an entity.

Some module layouts place a character's attributes in adjacent chunks without
repeating the character's main name. When the operator has verified that a
bounded chunk range belongs to one established entity, this script stores each
chunk as an approved candidate attached to that explicit target entity.

Deterministic on purpose: isolated attribute fragments can be ambiguous to a
small model, while the operator-provided entity and chunk range are explicit.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_kp.application.module_knowledge_service import ModuleKnowledgeService
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect


def run(args: argparse.Namespace) -> int:
    conn = connect(Path(args.db_path))
    try:
        repo = Repository(conn)
        knowledge = ModuleKnowledgeService(repo)

        # Load the attribute chunks.
        rows = repo.connection.execute(
            """
            SELECT id, order_index, text FROM module_chunks
            WHERE module_id = ? AND order_index BETWEEN ? AND ?
            ORDER BY order_index
            """,
            (args.module, args.from_order, args.to_order),
        ).fetchall()
        chunks = [dict(row) for row in rows]
        if not chunks:
            print("No chunks in the requested range; nothing to do.")
            return 0

        # Resolve the target entity (the NPC whose attributes these are).
        entity = next(
            (
                item
                for item in repo.list_module_entities(args.module)
                if item["name"] == args.entity_name
                and item["entity_type"] == args.entity_type
            ),
            None,
        )
        if entity is None:
            print(f"Entity not found: {args.entity_name} ({args.entity_type})")
            return 1

        attached = 0
        for chunk in chunks:
            text = str(chunk["text"]).strip()
            if not text:
                continue
            try:
                candidate = knowledge.create_manual_candidate(
                    args.module,
                    {
                        "kind": "module_canon",
                        "title": f"{args.entity_name}属性",
                        "statement": text,
                        "entity_name": args.entity_name,
                        "entity_type": args.entity_type,
                        "visibility": "kp",
                        "spoiler_tag": None,
                        "citations": [
                            {
                                "chunk_id": chunk["id"],
                                "asset_id": None,
                                "evidence_text": text[:1000],
                            }
                        ],
                    },
                )
            except (KeyError, TypeError, ValueError) as exc:
                print(f"[skip] order {chunk['order_index']}: {exc}")
                continue
            # Approve and attach to the entity (junction + description).
            try:
                knowledge.review_candidate(
                    candidate["id"],
                    decision="approved",
                    member_id=None,
                    note="operator-confirmed entity attributes (ai_kp)",
                )
                repo.link_candidate_to_entity(str(entity["id"]), str(candidate["id"]))
                repo.connection.execute(
                    """
                    UPDATE module_entities
                    SET description = description || char(10) || ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (text, entity["id"]),
                )
                repo.commit()
                attached += 1
                print(f"  ~ attached order {chunk['order_index']}: {text[:40]}")
            except (KeyError, ValueError, sqlite3.IntegrityError) as exc:
                print(f"[skip] attach order {chunk['order_index']}: {exc}")

        print(f"\nAttached {attached} attribute candidates to {args.entity_name}.")
        return 0
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministically attach NPC attribute chunks to an entity."
    )
    parser.add_argument("--module", required=True)
    parser.add_argument("--entity-name", required=True, help="target entity name")
    parser.add_argument("--entity-type", default="npc")
    parser.add_argument("--from-order", type=int, required=True)
    parser.add_argument("--to-order", type=int, required=True)
    parser.add_argument("--db-path", default="data/ai_kp.sqlite3")
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
