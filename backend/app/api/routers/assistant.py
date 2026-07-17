"""Assistant HTTP request and response mapping."""

from fastapi import APIRouter

from ...schemas.assistant import (
    AssistantEditPlanResponse,
    AssistantGalleryBatchJobStatus,
    AssistantGalleryImageResponse,
    AssistantGalleryMetadataResponse,
    AssistantHealthResponse,
    AssistantImagePromptResponse,
    AssistantJobDiagnoseResponse,
    AssistantPromptCheckResponse,
    AssistantPromptRewriteResponse,
    AssistantPromptVariantsResponse,
    AssistantRecommendParamsResponse,
)
from ...services import assistant_batch, assistant_text, assistant_vision

router = APIRouter()

router.add_api_route("/api/assistant/health", assistant_text.assistant_health, methods=["POST"], response_model=AssistantHealthResponse)
router.add_api_route("/api/assistant/prompt/rewrite", assistant_text.rewrite_prompt, methods=["POST"], response_model=AssistantPromptRewriteResponse)
router.add_api_route("/api/assistant/prompt/check", assistant_text.check_prompt, methods=["POST"], response_model=AssistantPromptCheckResponse)
router.add_api_route("/api/assistant/prompt/variants", assistant_text.prompt_variants, methods=["POST"], response_model=AssistantPromptVariantsResponse)
router.add_api_route("/api/assistant/generate/recommend-params", assistant_text.recommend_generate_params, methods=["POST"], response_model=AssistantRecommendParamsResponse)
router.add_api_route("/api/assistant/jobs/{job_id}/diagnose", assistant_text.diagnose_job, methods=["POST"], response_model=AssistantJobDiagnoseResponse)
router.add_api_route("/api/assistant/edit/plan", assistant_text.plan_edit, methods=["POST"], response_model=AssistantEditPlanResponse)
router.add_api_route("/api/assistant/image/prompt", assistant_vision.prompt_from_uploaded_image, methods=["POST"], response_model=AssistantImagePromptResponse)
router.add_api_route("/api/assistant/gallery/{image_id}/metadata", assistant_vision.get_gallery_metadata, methods=["GET"], response_model=AssistantGalleryMetadataResponse)
router.add_api_route("/api/assistant/gallery/batch/analyze", assistant_batch.batch_analyze_gallery, methods=["POST"], response_model=AssistantGalleryBatchJobStatus, status_code=202)
router.add_api_route("/api/assistant/gallery/batch/analyze/{job_id}", assistant_batch.get_batch_analyze_job, methods=["GET"], response_model=AssistantGalleryBatchJobStatus)
router.add_api_route("/api/assistant/gallery/{image_id}/describe", assistant_vision.describe_gallery_image, methods=["POST"], response_model=AssistantGalleryImageResponse)
router.add_api_route("/api/assistant/gallery/{image_id}/prompt", assistant_vision.prompt_gallery_image, methods=["POST"], response_model=AssistantGalleryImageResponse)
router.add_api_route("/api/assistant/gallery/{image_id}/analyze", assistant_vision.analyze_gallery_image, methods=["POST"], response_model=AssistantGalleryImageResponse)

