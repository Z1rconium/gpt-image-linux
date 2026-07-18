from .app_state import FRONTEND_BUILD_DIR, app
from .body_limit import BodyLimitMiddleware
from .middleware import TextOnlyGZipMiddleware, register_exception_handlers, register_middleware
from .routers import routers
from ..integrations.upstream import generation as proxy


register_middleware(app)
register_exception_handlers(app)
app.add_middleware(TextOnlyGZipMiddleware, minimum_size=1024)
app.add_middleware(BodyLimitMiddleware)

for router in routers:
    app.include_router(router)
