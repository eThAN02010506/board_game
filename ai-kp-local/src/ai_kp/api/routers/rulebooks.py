from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from ai_kp.api.authz import require_local_admin
from ai_kp.api.dependencies import get_app_settings, get_identity, get_repo
from ai_kp.api.schemas import RuleExecuteRequest, RuleQueryRequest
from ai_kp.api.uploads import read_limited_body, safe_upload_filename
from ai_kp.application.rulebook_service import RulebookService
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.document_process_sandbox import DocumentProcessPolicy
from ai_kp.infrastructure.knowledge.minirag import MiniRagOriginalIndex
from ai_kp.infrastructure.knowledge.pdf_ingestion import MAX_PDF_BYTES
from ai_kp.infrastructure.knowledge.rulebook_sandbox import (
    extract_rulebook_pdf_isolated,
)
from ai_kp.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from ai_kp.platform.sessions.models import AuthenticatedMember
from ai_kp.rule_authoring.models import RuleReviewSubmission, RuleStatus

router = APIRouter()


def _require_kp(identity: AuthenticatedMember) -> None:
    if identity.role != "kp":
        raise HTTPException(status_code=403, detail="KP access required")


def get_rulebook_service(
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> RulebookService:
    parse_policy = DocumentProcessPolicy(
        timeout_seconds=settings.module_parse_timeout_seconds,
        memory_limit_mib=settings.module_parse_memory_limit_mib,
        cpu_seconds=settings.module_parse_cpu_seconds,
    )
    return RulebookService(
        repo,
        extractor=lambda data, filename: extract_rulebook_pdf_isolated(
            data,
            filename,
            policy=parse_policy,
        ),
        index_factory=lambda ruleset_id, source_id: MiniRagOriginalIndex(
            settings.rulebook_index_root,
            ruleset_id,
            source_id,
            settings.rulebook_embedding_dimensions,
        ),
    )


@router.post("/rulebooks/sources")
async def ingest_rulebook(
    request: Request,
    ruleset_id: str = Query(default="coc7-keeper-cn-2002c", max_length=120),
    title: str | None = Query(default=None, max_length=200),
    x_file_name: str | None = Header(default=None),
    _admin: None = Depends(require_local_admin),
    service: RulebookService = Depends(get_rulebook_service),
) -> dict:
    data = await read_limited_body(request, max_bytes=MAX_PDF_BYTES, label="规则书 PDF")
    filename = safe_upload_filename(x_file_name, default="rulebook.pdf")
    return await run_in_threadpool(
        service.ingest_pdf,
        data,
        filename,
        ruleset_id=ruleset_id,
        title=title,
    )


@router.get("/rulebooks/sources")
def list_rulebooks(
    _admin: None = Depends(require_local_admin),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    return repo.list_rule_sources()


@router.get("/rulebooks/sources/{source_id}")
def get_rulebook(
    source_id: str,
    _admin: None = Depends(require_local_admin),
    repo: Repository = Depends(get_repo),
) -> dict:
    source = repo.get_rule_source(source_id)
    source["latest_run"] = repo.latest_ingestion_run(source_id)
    source["object_status_counts"] = {
        status: len(repo.list_rule_objects(source_id, status=status))
        for status in ("candidate", "validated", "review_required", "quarantined")
    }
    source["validation_issues"] = repo.list_rule_validation_issues(source_id)
    return source


@router.get("/rulebooks/sources/{source_id}/rules")
def list_rulebook_rules(
    source_id: str,
    rule_status: str | None = Query(default=None, alias="status"),
    rule_key: str | None = Query(default=None, max_length=160),
    _admin: None = Depends(require_local_admin),
    identity: AuthenticatedMember = Depends(get_identity),
    service: RulebookService = Depends(get_rulebook_service),
) -> list[dict]:
    _require_kp(identity)
    allowed_statuses = {status.value for status in RuleStatus}
    if rule_status is not None and rule_status not in allowed_statuses:
        raise HTTPException(status_code=422, detail="Invalid rule status")
    return service.list_rules(
        source_id,
        status=rule_status,
        rule_key=rule_key,
    )


@router.post("/rulebooks/rules/{rule_object_id}/review")
def review_rulebook_rule(
    rule_object_id: str,
    payload: RuleReviewSubmission,
    _admin: None = Depends(require_local_admin),
    identity: AuthenticatedMember = Depends(get_identity),
    service: RulebookService = Depends(get_rulebook_service),
) -> dict:
    _require_kp(identity)
    return service.review_rule(
        rule_object_id,
        payload,
        reviewer_member_id=identity.member_id,
    )


@router.post("/rulebooks/sources/{source_id}/index")
async def index_rulebook(
    source_id: str,
    _admin: None = Depends(require_local_admin),
    service: RulebookService = Depends(get_rulebook_service),
) -> dict:
    return await service.index_source(source_id)


@router.post("/rulebooks/sources/{source_id}/extract-rules")
async def extract_rulebook_rules(
    source_id: str,
    request: Request,
    limit: int = Query(default=10, ge=1, le=100),
    retry_failed: bool = Query(default=False),
    _admin: None = Depends(require_local_admin),
    settings: Settings = Depends(get_app_settings),
    service: RulebookService = Depends(get_rulebook_service),
) -> dict:
    llm = OpenAICompatibleClient(
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_model,
        max_tokens=8192,
        client=getattr(request.app.state, "http_client", None),
    )
    try:
        return await service.extract_rules(
            source_id,
            llm,
            model_name=settings.llm_model,
            limit=limit,
            retry_failed=retry_failed,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/rules/query")
async def query_rules(
    payload: RuleQueryRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    service: RulebookService = Depends(get_rulebook_service),
) -> dict:
    audience = "kp" if identity.role == "kp" else "player"
    return await service.query(
        ruleset_id=payload.ruleset_id,
        question=payload.question,
        audience=audience,
        top_k=payload.top_k,
    )


@router.post("/rules/execute")
def execute_rule(
    payload: RuleExecuteRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    service: RulebookService = Depends(get_rulebook_service),
) -> dict:
    return service.execute(
        ruleset_id=payload.ruleset_id,
        rule_key=payload.rule_key,
        inputs=payload.inputs,
        audience="kp" if identity.role == "kp" else "player",
    )
