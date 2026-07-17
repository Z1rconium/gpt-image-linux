"""Gallery route composition entry point."""

from fastapi import APIRouter

from .gallery_batch import router as batch_router
from .gallery_queries import router as queries_router
from .gallery_tasks import router as tasks_router

router = APIRouter()
router.include_router(batch_router)
router.include_router(tasks_router)
router.include_router(queries_router)
