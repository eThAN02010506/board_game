import asyncio
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings
from ai_kp.core.db import connect
from ai_kp.platform.ports.images import MapImageResult


def _png(width: int, height: int, seed: int | None = None) -> bytes:
    buffer = BytesIO()
    color = ((seed or 0) % 255, 90, 130)
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeImageProvider:
    provider_id = "fake-image"
    model_id = "period-map-v1"

    async def generate(self, request):
        return MapImageResult(
            content=_png(request.width, request.height, request.seed),
            mime_type="image/png",
            width=request.width,
            height=request.height,
            seed=request.seed,
        )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_period_map_image_realcase_survives_restart_and_enforces_asset_access(
    tmp_path: Path,
) -> None:
    asyncio.run(_exercise_period_map_realcase(tmp_path))


async def _exercise_period_map_realcase(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "realcase.sqlite3",
        map_asset_root=tmp_path / "map-assets",
        image_base_url="http://unused.local/v1",
        image_model="period-map-v1",
    )
    app = create_app(settings)
    app.state.map_image_provider = FakeImageProvider()
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 41234))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        campaign = (
            await client.post(
                "/campaigns",
                json={"title": "雾港 1928", "current_time": "1928-10-03 19:30"},
            )
        ).json()
        session = (
            await client.post(
                f"/campaigns/{campaign['id']}/sessions",
                json={"kp_display_name": "OldOnes"},
            )
        ).json()
        kp_headers = _bearer(session["access_token"])
        generated_response = await client.post(
            f"/campaigns/{campaign['id']}/maps/generate",
            headers=kp_headers,
            json={
                "title": "黑水镇警局",
                "prompt": "1928 年新英格兰警局的夜间调查地图。",
                "map_kind": "floorplan",
                "locations": ["街道入口", "接待大厅", "走廊", "问询室", "值班办公室", "楼梯"],
                "routes": [
                    ["街道入口", "接待大厅"],
                    ["接待大厅", "走廊"],
                    ["走廊", "问询室"],
                    ["走廊", "值班办公室"],
                    ["走廊", "楼梯"],
                ],
                "features": ["深色木制接待台", "机械打字机", "有线电话"],
                "required_elements": [
                    "街道入口",
                    "接待大厅",
                    "走廊",
                    "问询室",
                    "值班办公室",
                    "楼梯",
                    "深色木制接待台",
                    "机械打字机",
                    "有线电话",
                ],
                "era_year": 1928,
                "locale": "美国马萨诸塞州",
                "time_of_day": "夜晚",
                "weather": "冷雨",
                "public_architecture": ["新英格兰市政建筑", "红砖", "深色木材"],
                "forbidden_elements": ["霓虹招牌"],
            },
        )
        assert generated_response.status_code == 200
        saved_map = generated_response.json()
        assert saved_map["revision_no"] == 1
        assert saved_map["validation"]["coverage"]["percent"] == 100
        assert saved_map["map_spec"]["era"]["year"] == 1928

        prompt_response = await client.get(
            f"/maps/{saved_map['id']}/image-prompt",
            headers=kp_headers,
        )
        assert prompt_response.status_code == 200
        assert "1928" in prompt_response.json()["prompt"]
        assert "霓虹招牌" not in prompt_response.json()["prompt"]

        first_asset_response = await client.post(
            f"/maps/{saved_map['id']}/image-assets/generate",
            headers=kp_headers,
            json={"width": 1024, "height": 1024, "seed": 7},
        )
        second_asset_response = await client.post(
            f"/maps/{saved_map['id']}/image-assets/generate",
            headers=kp_headers,
            json={"width": 1024, "height": 1024, "seed": 8},
        )
        assert first_asset_response.status_code == 200
        assert second_asset_response.status_code == 200
        first_asset = first_asset_response.json()
        second_asset = second_asset_response.json()

        selected = await client.post(
            f"/maps/{saved_map['id']}/assets/{first_asset['id']}/select",
            headers=kp_headers,
        )
        audit_connection = connect(settings.db_path)
        try:
            draft_background_event = audit_connection.execute(
                """
                SELECT audience
                FROM realtime_events
                WHERE event_type = 'map.changed'
                  AND resource_id = ?
                  AND json_extract(payload_json, '$.background_changed') = 1
                ORDER BY id DESC
                LIMIT 1
                """,
                (saved_map["id"],),
            ).fetchone()
        finally:
            audit_connection.close()
        assert draft_background_event is not None
        assert draft_background_event["audience"] == "kp"
        missing_snapshot_publish = await client.post(
            f"/maps/{saved_map['id']}/publish",
            headers=kp_headers,
        )
        stale_publish = await client.post(
            f"/maps/{saved_map['id']}/publish",
            headers=kp_headers,
            json={
                "expected_revision_id": saved_map["revision_id"],
                "expected_selected_asset_id": second_asset["id"],
            },
        )
        published = await client.post(
            f"/maps/{saved_map['id']}/publish",
            headers=kp_headers,
            json={
                "expected_revision_id": saved_map["revision_id"],
                "expected_selected_asset_id": first_asset["id"],
            },
        )
        player = (
            await client.post(
                "/sessions/join",
                json={
                    "join_code": session["join_code"],
                    "display_name": "调查员",
                },
            )
        ).json()
        player_headers = _bearer(player["access_token"])
        player_map_response = await client.get(
            f"/maps/{saved_map['id']}?view=player",
            headers=player_headers,
        )
        selected_content = await client.get(
            f"/map-assets/{first_asset['id']}/content",
            headers=player_headers,
        )
        unselected_content = await client.get(
            f"/map-assets/{second_asset['id']}/content",
            headers=player_headers,
        )

        assert selected.status_code == 200
        assert missing_snapshot_publish.status_code == 422
        assert stale_publish.status_code == 409
        assert published.status_code == 200
        assert player_map_response.status_code == 200
        player_map = player_map_response.json()
        assert player_map["render"]["selected_asset_id"] == first_asset["id"]
        assert "scene_brief" not in player_map["map_spec"]
        assert "provenance" not in player_map["map_spec"]
        assert "forbidden_visuals" not in player_map["map_spec"]["era"]
        for private_key in (
            "revision_id",
            "revision_no",
            "spec_hash",
            "layout_hash",
            "current_revision_id",
            "selected_public_asset_id",
            "reveal_version",
        ):
            assert private_key not in player_map
        assert "assets" not in player_map
        assert selected_content.status_code == 200
        assert selected_content.headers["content-type"] == "image/png"
        assert unselected_content.status_code == 404

    restarted = create_app(settings)
    restarted_transport = httpx.ASGITransport(
        app=restarted,
        client=("127.0.0.1", 41235),
    )
    async with httpx.AsyncClient(
        transport=restarted_transport,
        base_url="http://127.0.0.1",
    ) as restarted_client:
        restored_map = await restarted_client.get(
            f"/maps/{saved_map['id']}?view=player",
            headers=player_headers,
        )
        restored_asset = await restarted_client.get(
            f"/map-assets/{first_asset['id']}/content",
            headers=player_headers,
        )

    assert restored_map.status_code == 200
    assert "revision_id" not in restored_map.json()
    assert restored_asset.status_code == 200
    assert restored_asset.content == _png(1024, 1024, 7)
