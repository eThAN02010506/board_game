"""Generate, cache, and persist player-safe map background candidates."""

import secrets

from ai_kp.application.errors import KpSessionEndedError
from ai_kp.application.ports.repositories import MapImageStore
from ai_kp.platform.ports.images import (
    MapAssetStore,
    MapImageProvider,
    MapImageRequest,
)
from ai_kp.platform.scenes.map_spec import (
    build_image_prompt,
    image_generation_input_hash,
)


class MapImageService:
    def __init__(
        self,
        repo: MapImageStore,
        provider: MapImageProvider,
        asset_store: MapAssetStore,
    ):
        self.repo = repo
        self.provider = provider
        self.asset_store = asset_store

    async def generate_public_background(
        self,
        map_id: str,
        *,
        width: int,
        height: int,
        seed: int | None,
        member_id: str,
        session_id: str,
    ) -> tuple[dict, bool]:
        revision = self.repo.get_current_map_revision(map_id)
        if revision is None:
            raise ValueError("旧地图尚未建立 MapSpec revision，不能生成背景图")
        revision_id = str(revision["id"])
        spec = revision["spec"]
        prompt = build_image_prompt(spec, "table")
        resolved_seed = seed if seed is not None else secrets.randbelow(2_147_483_648)
        generation_hash = image_generation_input_hash(
            spec,
            provider=self.provider.provider_id,
            model=self.provider.model_id,
            width=width,
            height=height,
            seed=resolved_seed,
        )
        cached = self.repo.find_map_asset_by_generation_hash(map_id, generation_hash)
        if cached is not None and self.asset_store.exists(str(cached["storage_path"])):
            return cached, True

        generated = await self.provider.generate(
            MapImageRequest(
                prompt=prompt,
                width=width,
                height=height,
                seed=resolved_seed,
            )
        )
        if generated.width != width or generated.height != height:
            raise RuntimeError(
                "图片模型返回尺寸与请求不一致："
                f"请求 {width}x{height}，收到 {generated.width}x{generated.height}"
            )

        # The provider call intentionally runs without a write lock. Reclaim a short
        # transaction afterwards and revalidate both authority and the source revision.
        self.repo.begin_immediate()
        if not self.repo.is_session_member_active(member_id, session_id):
            raise KpSessionEndedError("KP session ended while the image model was running")
        current_revision = self.repo.get_current_map_revision(map_id)
        if current_revision is None or current_revision["id"] != revision_id:
            raise ValueError("地图版本在图片生成期间发生变化，请基于最新版本重新生成")

        # Another request may have won the same hash while this provider call was running.
        cached = self.repo.find_map_asset_by_generation_hash(map_id, generation_hash)
        if cached is not None and self.asset_store.exists(str(cached["storage_path"])):
            return cached, True

        stored = self.asset_store.write(generated.content)
        if stored.width != width or stored.height != height:
            raise RuntimeError(
                "图片文件实际尺寸与请求不一致："
                f"请求 {width}x{height}，收到 {stored.width}x{stored.height}"
            )
        values = {
            "revision_id": revision_id,
            "content_hash": stored.content_hash,
            "storage_path": stored.storage_path,
            "mime_type": stored.mime_type,
            "width": stored.width,
            "height": stored.height,
            "provider": self.provider.provider_id,
            "model": self.provider.model_id,
            "seed": generated.seed if generated.seed is not None else resolved_seed,
            "parameters": {
                "requested_width": width,
                "requested_height": height,
                "size_bytes": stored.size_bytes,
                "provider_metadata": generated.metadata,
            },
            "prompt_text": prompt,
        }
        if cached is not None:
            asset = self.repo.repair_map_asset(cached["id"], **values)
        else:
            asset = self.repo.create_map_asset(
                map_id=map_id,
                generation_input_hash=generation_hash,
                **values,
            )
        return asset, False
