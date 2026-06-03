from starlette.middleware.gzip import GZipMiddleware

from .app_state import FRONTEND_BUILD_DIR, app
from .body_limit import BodyLimitMiddleware
from .middleware import register_exception_handlers, register_middleware
from .routers import routers
from ..integrations import upstream_client as proxy
from ..repositories import storage


register_middleware(app)
register_exception_handlers(app)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(BodyLimitMiddleware)

for router in routers:
    app.include_router(router)
