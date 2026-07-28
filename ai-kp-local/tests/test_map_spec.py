import asyncio
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from pydantic import ValidationError

from ai_kp.api.schemas import MapGenerateRequest
from ai_kp.application.errors import KpSessionEndedError
from ai_kp.application.map_image_service import MapImageService
from ai_kp.application.session_service import SessionService
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.infrastructure.images.storage import MapAssetFileStore
from ai_kp.maps.generation import generate_map
from ai_kp.platform.ports.images import MapImageResult
from ai_kp.platform.scenes.map_spec import (
    build_image_prompt,
    build_map_spec,
    canonical_json,
    map_spec_hash,
    project_map_spec,
    require_valid_map_spec,
)


def _png(width: int, height: int, seed: int | None = None) -> bytes:
    buffer = BytesIO()
    color = ((seed or 0) % 255, 80, 120)
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeImageProvider:
    provider_id = "fake-image"
    model_id = "fixture-v1"

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    async def generate(self, request):
        self.calls += 1
        self.prompts.append(request.prompt)
        return MapImageResult(
            content=_png(request.width, request.height, request.seed),
            mime_type="image/png",
            width=request.width,
            height=request.height,
            seed=request.seed,
        )


def _period_spec() -> dict:
    return build_map_spec(
        title="黑水镇警局",
        prompt="一座 1928 年的新英格兰小镇警局，地下档案室属于 KP 秘密。",
        style="investigation",
        width=960,
        height=640,
        locations=[
            {
                "name": "接待大厅",
                "x": 220,
                "y": 260,
                "visibility": "table",
                "notes": "",
            },
            {
                "name": "地下档案室",
                "x": 720,
                "y": 420,
                "visibility": "kp",
                "notes": "暗门后方",
            },
        ],
        routes=[
            {
                "start": "接待大厅",
                "end": "地下档案室",
                "visibility": "kp",
            }
        ],
        map_kind="floorplan",
        era_year=1928,
        locale="美国马萨诸塞州",
        public_architecture=["新英格兰公共市政建筑", "红砖"],
        feature_names=["深色木制接待台"],
        required_elements=["接待大厅", "地下档案室", "深色木制接待台"],
        forbidden_elements=["不要画出地下祭坛与克苏鲁神像"],
    )


def test_map_spec_hash_is_canonical_and_required_coverage_is_deterministic() -> None:
    spec = _period_spec()
    reordered = {key: spec[key] for key in reversed(tuple(spec))}

    report = require_valid_map_spec(spec)

    assert report.coverage_percent == 100
    assert map_spec_hash(spec) == map_spec_hash(reordered)
    assert canonical_json(spec) == canonical_json(reordered)


def test_player_projection_and_image_prompt_do_not_contain_kp_elements() -> None:
    spec = _period_spec()

    player_spec = project_map_spec(spec, ("player", "table"))
    prompt = build_image_prompt(spec, "table")

    assert "地下档案室" not in canonical_json(player_spec)
    assert "暗门" not in canonical_json(player_spec)
    assert "地下祭坛" not in canonical_json(player_spec)
    assert "克苏鲁神像" not in canonical_json(player_spec)
    assert "地下档案室" not in prompt
    assert "暗门" not in prompt
    assert "地下祭坛" not in prompt
    assert "克苏鲁神像" not in prompt
    assert "1928" in prompt
    assert "LED lighting" in prompt
    assert "新英格兰公共市政建筑" in canonical_json(player_spec)
    assert "新英格兰公共市政建筑" in prompt
    assert "深色木制接待台" in prompt
    assert "地下祭坛" in canonical_json(spec)


def test_map_spec_rejects_unknown_routes_and_missing_required_elements() -> None:
    unknown_route = build_map_spec(
        title="错误地图",
        prompt="测试错误路线",
        style="investigation",
        width=960,
        height=640,
        locations=[{"name": "入口", "x": 100, "y": 100}],
        routes=[{"start": "入口", "end": "不存在的房间"}],
        required_elements=["入口", "保险柜"],
    )

    with pytest.raises(ValueError, match="不存在的地点.*保险柜"):
        require_valid_map_spec(unknown_route)


def test_map_request_requires_architecture_to_be_explicitly_public() -> None:
    with pytest.raises(ValidationError, match="architecture"):
        MapGenerateRequest.model_validate(
            {
                "title": "秘密地点",
                "prompt": "用于验证公共边界",
                "architecture": ["秘密地下祭坛的建筑结构"],
            }
        )

    request = MapGenerateRequest.model_validate(
        {
            "title": "公开地点",
            "prompt": "用于验证公共边界",
            "public_architecture": ["红砖公共建筑"],
        }
    )
    assert request.public_architecture == ["红砖公共建筑"]


def test_generated_background_is_content_addressed_and_cacheable(tmp_path: Path) -> None:
    asyncio.run(_exercise_generated_background_cache(tmp_path))


def test_image_generation_revalidates_kp_session_before_persisting(
    tmp_path: Path,
) -> None:
    asyncio.run(_exercise_closed_session_during_image_generation(tmp_path))


async def _exercise_closed_session_during_image_generation(tmp_path: Path) -> None:
    with db_session(tmp_path / "closed-session.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("会话竞态")
        session = SessionService(repo).create(campaign["id"])
        identity = repo.authenticate_access_token(session["access_token"])
        assert identity is not None
        generated = generate_map(
            title="测试地图",
            prompt="入口、走廊",
            location_names=["入口", "走廊"],
            routes=[("入口", "走廊")],
        )
        saved = repo.create_map(campaign["id"], generated)

        class ClosingProvider(FakeImageProvider):
            async def generate(self, request):
                repo.close_campaign_session(identity.session_id)
                return await super().generate(request)

        with pytest.raises(KpSessionEndedError):
            await MapImageService(
                repo,
                ClosingProvider(),
                MapAssetFileStore(tmp_path / "closed-assets"),
            ).generate_public_background(
                saved["id"],
                width=1024,
                height=1024,
                seed=1,
                member_id=identity.member_id,
                session_id=identity.session_id,
            )
        assert connection.execute("SELECT COUNT(*) FROM map_assets").fetchone()[0] == 0
        assert not (tmp_path / "closed-assets").exists()


async def _exercise_generated_background_cache(tmp_path: Path) -> None:
    provider = FakeImageProvider()
    with db_session(tmp_path / "maps.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("雾港 1928", current_time="1928-10-03")
        session = SessionService(repo).create(campaign["id"])
        identity = repo.authenticate_access_token(session["access_token"])
        assert identity is not None
        generated = generate_map(
            title="旧码头",
            prompt="旧码头、仓库、报社",
            location_names=["旧码头", "仓库", "报社"],
            routes=[("旧码头", "仓库"), ("仓库", "报社")],
            era_year=1928,
            locale="美国马萨诸塞州",
            feature_names=["煤气路灯", "木箱"],
        )
        saved = repo.create_map(campaign["id"], generated)
        service = MapImageService(
            repo,
            provider,
            MapAssetFileStore(tmp_path / "assets"),
        )

        first, first_cache_hit = await service.generate_public_background(
            saved["id"],
            width=1024,
            height=1024,
            seed=42,
            member_id=identity.member_id,
            session_id=identity.session_id,
        )
        second, second_cache_hit = await service.generate_public_background(
            saved["id"],
            width=1024,
            height=1024,
            seed=42,
            member_id=identity.member_id,
            session_id=identity.session_id,
        )
        cached_path = tmp_path / "assets" / first["storage_path"]
        cached_path.unlink()
        repaired, repaired_cache_hit = await service.generate_public_background(
            saved["id"],
            width=1024,
            height=1024,
            seed=42,
            member_id=identity.member_id,
            session_id=identity.session_id,
        )
        with patch(
            "ai_kp.application.map_image_service.secrets.randbelow",
            side_effect=(101, 102),
        ):
            automatic_first, _ = await service.generate_public_background(
                saved["id"],
                width=1024,
                height=1024,
                seed=None,
                member_id=identity.member_id,
                session_id=identity.session_id,
            )
            automatic_second, _ = await service.generate_public_background(
                saved["id"],
                width=1024,
                height=1024,
                seed=None,
                member_id=identity.member_id,
                session_id=identity.session_id,
            )
        repo.select_public_map_asset(saved["id"], first["id"])
        restored = repo.get_map(saved["id"])

    assert first_cache_hit is False
    assert second_cache_hit is True
    assert repaired_cache_hit is False
    assert first["id"] == second["id"]
    assert repaired["id"] == first["id"]
    assert automatic_first["id"] != automatic_second["id"]
    assert automatic_first["seed"] == 101
    assert automatic_second["seed"] == 102
    assert provider.calls == 4
    assert Path(tmp_path / "assets" / first["storage_path"]).is_file()
    assert restored["render"]["selected_asset_id"] == first["id"]
    assert restored["revision_no"] == 1
    assert restored["validation"]["coverage"]["percent"] == 100
