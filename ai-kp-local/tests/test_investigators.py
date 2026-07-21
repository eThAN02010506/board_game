import json
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.characters.xlsx_import import (
    MAX_XLSX_UNCOMPRESSED_BYTES,
    import_coc_character_xlsx,
)
from ai_kp.core.config import Settings
from ai_kp.rules.coc7_character import normalize_character_sheet
from ai_kp.rules.coc7_skills import list_coc7_skill_catalog


def _xlsx_fixture() -> bytes:
    workbook = """<?xml version="1.0" encoding="UTF-8"?>
    <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <sheets><sheet name="人物卡" sheetId="1" r:id="rId1"/></sheets>
    </workbook>"""
    relationships = """<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1"
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
        Target="worksheets/sheet1.xml"/>
    </Relationships>"""

    def inline(reference: str, text: str) -> str:
        return f'<c r="{reference}" t="inlineStr"><is><t>{text}</t></is></c>'

    def number(reference: str, value: int) -> str:
        return f'<c r="{reference}"><v>{value}</v></c>'

    cells = [
        inline("B2", "调查员信息"),
        inline("C3", "徐闻"),
        inline("J3", "力量 STR"),
        number("K3", 60),
        number("N3", 70),
        number("Q3", 55),
        number("K5", 50),
        number("N5", 45),
        number("Q5", 65),
        number("K7", 60),
        number("N7", 80),
        number("M10", 50),
        number("C6", 28),
        inline("G4", "1920s"),
        inline("G5", "记者"),
        inline("D18", "侦查"),
        number("G18", 25),
        number("I18", 20),
        number("J18", 10),
        '<c r="K18"><f>SUM(G18:J18)</f><v>999</v></c>',
    ]
    sheet = f"""<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <sheetData><row r="2">{''.join(cells)}</row></sheetData>
    </worksheet>"""
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def test_excel_import_ignores_formula_totals_and_recomputes_derived_values() -> None:
    preview = import_coc_character_xlsx(_xlsx_fixture(), "徐闻.xlsx")
    sheet = preview["canonical_sheet"]

    assert sheet["identity"]["name"] == "徐闻"
    assert sheet["derived"]["max_hp"] == 11
    assert sheet["derived"]["max_mp"] == 11
    assert sheet["derived"]["dodge"] == 35
    assert sheet["skills"][0]["current_value"] == 55
    assert preview["ignored_formula_cells"] == 1


def test_numbered_fighting_slot_without_specialization_has_no_brawl_base() -> None:
    from ai_kp.characters.xlsx_import import _skill_base

    attributes = {"dex": 60, "edu": 60}

    assert _skill_base("格斗：", "斗殴", None, attributes) == 25
    assert _skill_base("格斗①", "", None, attributes) == 0


def test_skill_catalog_matches_uploaded_template_slots() -> None:
    catalog = list_coc7_skill_catalog()

    assert len(catalog) == 67
    assert len({entry["skill_key"] for entry in catalog}) == 67
    assert all(entry["description"] for entry in catalog)
    assert catalog[0]["display_name"] == "信用评级"
    assert catalog[-1]["display_name"] == "驯兽"
    assert next(entry for entry in catalog if entry["display_name"] == "闪避")[
        "base_formula"
    ] == "dex_half"
    assert next(entry for entry in catalog if entry["display_name"] == "母语")[
        "base_formula"
    ] == "edu"
    assert not next(
        entry for entry in catalog if entry["display_name"] == "克苏鲁神话"
    )["creation_points_allowed"]


def test_manual_skill_budgets_and_creation_limits_are_validated() -> None:
    skills = [
        {
            "skill_key": "coc7.spot_hidden",
            "display_name": "侦查",
            "base_value": 25,
            "occupation_points": 220,
            "interest_points": 150,
        },
        {
            "skill_key": "coc7.cthulhu_mythos",
            "display_name": "克苏鲁神话",
            "base_value": 0,
            "interest_points": 1,
        },
    ]
    canonical, warnings = normalize_character_sheet(
        {
            "identity": {"name": "加点测试", "age": 25},
            "characteristics": {
                "str": 50,
                "con": 50,
                "siz": 50,
                "dex": 60,
                "app": 50,
                "int": 60,
                "pow": 50,
                "edu": 50,
                "luck": 50,
            },
            "skills": skills,
            "provenance": {
                "source_type": "manual",
                "occupation_point_formula": "edu4",
            },
        }
    )

    assert canonical["skills"][0]["current_value"] == 395
    assert any("职业技能点超出预算 20 点" in warning for warning in warnings)
    assert any("兴趣技能点超出预算 31 点" in warning for warning in warnings)
    assert any("克苏鲁神话不能" in warning for warning in warnings)


def test_excel_import_rejects_excessive_uncompressed_content() -> None:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", b"x" * (MAX_XLSX_UNCOMPRESSED_BYTES + 1))

    try:
        import_coc_character_xlsx(output.getvalue(), "compressed-bomb.xlsx")
    except ValueError as error:
        assert "解压后超过 32 MiB" in str(error)
    else:
        raise AssertionError("Oversized uncompressed XLSX content was accepted")


def test_player_profile_owns_immutable_investigator_revisions(tmp_path: Path) -> None:
    app = create_app(Settings(db_path=tmp_path / "investigators.sqlite3"))
    with TestClient(app) as client:
        profile_bundle = client.post(
            "/player-profiles", json={"display_name": "玩家甲"}
        ).json()
        player_headers = {"X-AI-KP-Player-Token": profile_bundle["player_token"]}
        preview_response = client.post(
            "/investigator-imports/preview",
            headers={**player_headers, "X-File-Name": "character.xlsx"},
            content=_xlsx_fixture(),
        )
        assert preview_response.status_code == 200
        preview = preview_response.json()
        create_payload = {
            "canonical_sheet": preview["canonical_sheet"],
            "source_type": "xlsx",
            "source_hash": preview["source_hash"],
            "source_filename": preview["source_filename"],
            "template_id": preview["template_id"],
            "parser_version": preview["parser_version"],
            "warnings": preview["warnings"],
        }
        created = client.post(
            "/investigators", headers=player_headers, json=create_payload
        )
        assert created.status_code == 200
        investigator = created.json()
        first_revision_id = investigator["current_revision_id"]
        assert investigator["current_revision"]["revision_no"] == 1

        revised_sheet = json.loads(json.dumps(preview["canonical_sheet"]))
        revised_sheet["identity"]["occupation"] = "私家侦探"
        revised = client.post(
            f"/investigators/{investigator['id']}/revisions",
            headers=player_headers,
            json={"canonical_sheet": revised_sheet, "source_type": "manual"},
        )
        assert revised.status_code == 200
        assert revised.json()["current_revision"]["revision_no"] == 2
        assert revised.json()["current_revision_id"] != first_revision_id

        listed = client.get("/investigators", headers=player_headers)
        assert listed.status_code == 200
        assert listed.json()[0]["current_revision"]["canonical_sheet"]["identity"][
            "occupation"
        ] == "私家侦探"

        other = client.post("/player-profiles", json={"display_name": "玩家乙"}).json()
        denied = client.get(
            f"/investigators/{investigator['id']}",
            headers={"X-AI-KP-Player-Token": other["player_token"]},
        )
        assert denied.status_code == 404


def test_campaign_submission_review_binding_and_runtime_state(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            db_path=tmp_path / "campaign-investigators.sqlite3",
            local_admin_enabled=False,
            admin_token="investigator-test-admin",
        )
    )
    with TestClient(app) as client:
        admin_headers = {"X-AI-KP-Admin-Token": "investigator-test-admin"}
        campaign = client.post(
            "/campaigns", headers=admin_headers, json={"title": "雾港测试团"}
        ).json()
        session = client.post(
            f"/campaigns/{campaign['id']}/sessions",
            headers=admin_headers,
            json={"kp_display_name": "测试 KP"},
        ).json()
        kp_headers = {"Authorization": f"Bearer {session['access_token']}"}
        profile = client.post(
            "/player-profiles", json={"display_name": "玩家甲"}
        ).json()
        player = client.post(
            "/sessions/join",
            json={
                "join_code": session["join_code"],
                "display_name": "玩家甲",
            },
        ).json()
        player_headers = {
            "Authorization": f"Bearer {player['access_token']}",
            "X-AI-KP-Player-Token": profile["player_token"],
        }
        preview = import_coc_character_xlsx(_xlsx_fixture(), "徐闻.xlsx")
        created = client.post(
            "/investigators",
            headers=player_headers,
            json={
                "canonical_sheet": preview["canonical_sheet"],
                "source_type": "manual",
            },
        ).json()

        submitted = client.post(
            f"/campaigns/{campaign['id']}/investigators/{created['id']}/submit",
            headers=player_headers,
            json={},
        )
        assert submitted.status_code == 200
        assert submitted.json()["status"] == "submitted"
        assert submitted.json()["approved_revision"] is None
        assert submitted.json()["diff"]

        before_approval = client.get(
            f"/campaigns/{campaign['id']}/pcs", headers=kp_headers
        )
        assert before_approval.status_code == 200
        assert before_approval.json() == []

        missing_comment = client.post(
            f"/campaigns/{campaign['id']}/investigators/{created['id']}/review",
            headers=kp_headers,
            json={"action": "changes_requested"},
        )
        assert missing_comment.status_code == 409

        changes_requested = client.post(
            f"/campaigns/{campaign['id']}/investigators/{created['id']}/review",
            headers=kp_headers,
            json={"action": "changes_requested", "comment": "请补充职业说明"},
        )
        assert changes_requested.status_code == 200
        assert changes_requested.json()["review_comment"] == "请补充职业说明"

        revised_sheet = json.loads(json.dumps(preview["canonical_sheet"]))
        revised_sheet["identity"]["occupation"] = "调查记者"
        revised = client.post(
            f"/investigators/{created['id']}/revisions",
            headers=player_headers,
            json={"canonical_sheet": revised_sheet, "source_type": "manual"},
        ).json()
        second_revision_id = revised["current_revision_id"]
        resubmitted = client.post(
            f"/campaigns/{campaign['id']}/investigators/{created['id']}/submit",
            headers=player_headers,
            json={"revision_id": second_revision_id},
        )
        assert resubmitted.status_code == 200
        assert resubmitted.json()["submitted_revision"]["revision_no"] == 2

        approved = client.post(
            f"/campaigns/{campaign['id']}/investigators/{created['id']}/review",
            headers=kp_headers,
            json={"action": "approved", "comment": "允许入团"},
        )
        assert approved.status_code == 200
        approved_record = approved.json()
        assert approved_record["status"] == "approved"
        assert approved_record["approved_revision_id"] == second_revision_id
        assert approved_record["campaign_state"]["current_hp"] == 11
        assert approved_record["campaign_state"]["state_version"] == 0

        public_cards = client.get(
            f"/campaigns/{campaign['id']}/investigators/public",
            headers={"Authorization": f"Bearer {player['access_token']}"},
        )
        assert public_cards.status_code == 200
        assert public_cards.json()[0]["public_summary"]["name"] == "徐闻"
        assert "canonical_sheet" not in json.dumps(public_cards.json())

        assigned = client.post(
            f"/sessions/{session['session']['id']}/members/"
            f"{player['member']['id']}/assign-investigator",
            headers=kp_headers,
            json={"investigator_id": created["id"]},
        )
        assert assigned.status_code == 200
        assert assigned.json()["pc_id"] == approved_record["legacy_pc_id"]
        refreshed_identity = client.get(
            "/auth/me", headers={"Authorization": f"Bearer {player['access_token']}"}
        )
        assert refreshed_identity.json()["pc_id"] == approved_record["legacy_pc_id"]

        observer = client.post(
            "/sessions/join",
            json={
                "join_code": session["join_code"],
                "display_name": "旁观玩家",
            },
        ).json()
        observer_cards = client.get(
            f"/campaigns/{campaign['id']}/pcs",
            headers={"Authorization": f"Bearer {observer['access_token']}"},
        )
        assert observer_cards.status_code == 200
        assert observer_cards.json()[0]["public_summary"]["occupation"] == "调查记者"
        assert "sheet" not in observer_cards.json()[0]

        updated_state = client.patch(
            f"/campaigns/{campaign['id']}/investigators/{created['id']}/state",
            headers=kp_headers,
            json={
                "expected_version": 0,
                "current_hp": 7,
                "conditions": [{"key": "major_wound", "label": "重伤"}],
            },
        )
        assert updated_state.status_code == 200
        assert updated_state.json()["current_hp"] == 7
        assert updated_state.json()["state_version"] == 1
        stale_update = client.patch(
            f"/campaigns/{campaign['id']}/investigators/{created['id']}/state",
            headers=kp_headers,
            json={"expected_version": 0, "current_hp": 6},
        )
        assert stale_update.status_code == 409

        third_sheet = json.loads(json.dumps(revised_sheet))
        third_sheet["identity"]["occupation"] = "私家侦探"
        third_revision = client.post(
            f"/investigators/{created['id']}/revisions",
            headers=player_headers,
            json={"canonical_sheet": third_sheet, "source_type": "manual"},
        ).json()
        pending_upgrade = client.post(
            f"/campaigns/{campaign['id']}/investigators/{created['id']}/submit",
            headers=player_headers,
            json={"revision_id": third_revision["current_revision_id"]},
        ).json()
        assert pending_upgrade["status"] == "submitted"
        assert pending_upgrade["approved_revision_id"] == second_revision_id
        assert pending_upgrade["campaign_state"]["current_hp"] == 7

        old_approval_still_bindable = client.post(
            f"/sessions/{session['session']['id']}/members/"
            f"{player['member']['id']}/assign-investigator",
            headers=kp_headers,
            json={"investigator_id": created["id"]},
        )
        assert old_approval_still_bindable.status_code == 200

        owner_view = client.get(
            f"/campaigns/{campaign['id']}/my-investigators",
            headers=player_headers,
        )
        assert owner_view.status_code == 200
        assert owner_view.json()[0]["review_comment"] is None
        assert len(owner_view.json()[0]["reviews"]) == 5
