import json

from ai_kp.core.ids import new_id


class ContextAssemblyRepository:
    """Persistence methods for inspecting exactly what an AI turn received."""

    def create_context_assembly(
        self,
        *,
        proposal_id: str,
        campaign_id: str,
        visibility_scope: str,
        final_prompt: list[dict[str, str]],
        included_sources: list[dict],
        excluded_sources: list[dict],
        token_estimate: int,
    ) -> dict:
        assembly_id = new_id("ctx")
        self.connection.execute(
            """
            INSERT INTO context_assemblies
              (id, proposal_id, campaign_id, visibility_scope, final_prompt_json,
               included_sources_json, excluded_sources_json, token_estimate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assembly_id,
                proposal_id,
                campaign_id,
                visibility_scope,
                json.dumps(final_prompt, ensure_ascii=False),
                json.dumps(included_sources, ensure_ascii=False),
                json.dumps(excluded_sources, ensure_ascii=False),
                token_estimate,
            ),
        )
        return self.get_context_assembly(proposal_id)

    def get_context_assembly(self, proposal_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM context_assemblies WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["final_prompt"] = json.loads(result.pop("final_prompt_json"))
        result["included_sources"] = json.loads(result.pop("included_sources_json"))
        result["excluded_sources"] = json.loads(result.pop("excluded_sources_json"))
        return result
