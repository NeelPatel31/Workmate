from .internals import internals_router
from .stream import stream_router

routes = [
    internals_router,
    stream_router,
]
