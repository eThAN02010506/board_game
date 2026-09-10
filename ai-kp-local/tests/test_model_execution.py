from pathlib import Path

import pytest

from ai_kp.bootstrap.settings import Settings
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.infrastructure.llm.model_execution import (
    ModelExecutionSnapshot,
    ModelExecutionSuperseded,
)


def _save_model(repo: Repository, *, base_url: str, model: str) -> dict:
    return repo.save_model_configuration(
        provider_type="openai_compatible",
        base_url=base_url,
        api_key="test-key",
        model=model,
        local_model_path=None,
        local_port=8011,
        semantic_profile="small",
    )


def test_background_model_execution_uses_persisted_configuration(
    tmp_path: Path,
) -> None:
    fallback = Settings(
        db_path=tmp_path / "model-execution.sqlite3",
        llm_base_url="http://startup-model.test/v1",
        llm_api_key="startup",
        llm_model="startup-model",
    )
    with db_session(fallback.db_path) as connection:
        repo = Repository(connection)
        saved = _save_model(
            repo,
            base_url="http://selected-model.test/v1",
            model="selected-model",
        )
        execution = ModelExecutionSnapshot.capture(repo, fallback)

        assert execution.configuration_version == saved["version"]
        assert execution.settings.llm_base_url == "http://selected-model.test/v1"
        assert execution.settings.llm_model == "selected-model"


def test_background_model_execution_rejects_old_generation(tmp_path: Path) -> None:
    settings = Settings(db_path=tmp_path / "model-fence.sqlite3")
    with db_session(settings.db_path) as connection:
        repo = Repository(connection)
        _save_model(repo, base_url="http://model-a.test/v1", model="model-a")
        execution = ModelExecutionSnapshot.capture(repo, settings)
        _save_model(repo, base_url="http://model-b.test/v1", model="model-b")

        with pytest.raises(ModelExecutionSuperseded):
            execution.revalidate(repo)
