from fasthtml.common import fast_app, serve, Title, NotStr, Div
from dotenv import load_dotenv
from starlette.middleware.base import BaseHTTPMiddleware
import os

from app.implementation.components.index_page import INDEX_BODY
from app.implementation.api import api

load_dotenv()

if os.environ.get("DATABASE_URL"):
    print("Main: DATABASE_URL found in environment.")
else:
    print("Main: DATABASE_URL NOT found. Using local SQLite.")

# public/photos/*.jpg is served at /photos/*.jpg; no favicon yet -- add one to
# public/favicon.ico and an hdrs=(Link(...),) tuple here when we have real branding.
app, rt = fast_app(static_path="public")

# Mount the FastAPI JSON API (the migrated lustereczko custom tools) under /api.
# FastHTML's app is Starlette-based, same as FastAPI, so a plain ASGI mount works.
app.mount("/api", api)


class PhotoCacheMiddleware(BaseHTTPMiddleware):
    """FastHTML's static_path serving (a per-file FileResponse route, not a
    StaticFiles mount) sets ETag/Last-Modified but no Cache-Control, so
    browsers revalidate every photo over the network on every view instead
    of serving straight from disk. Photo content is immutable once added
    (a changed image gets a new filename/seq, per seed_data conventions) --
    unlike "/", which changes with every UI deploy and must stay
    uncached, so this only touches /photos/*."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/photos/"):
            response.headers["Cache-Control"] = "public, max-age=2592000"
        return response


app.add_middleware(PhotoCacheMiddleware)


@rt("/")
def get():
    return Title("Caribbean Fish Recall"), Div(
        NotStr(INDEX_BODY),
        style="background:#04202e; min-height:100vh; padding:1px 0;",
    )


@rt("/claim/{token}")
def get_claim(token: str):
    # Same page shell as "/" -- the transfer-link flow is entirely
    # client-side (index_page.py's JS detects the /claim/ path itself and
    # runs the preview/confirm flow), so the server doesn't need to look at
    # `token` at all here; it's only read by the frontend from the URL.
    return Title("Caribbean Fish Recall"), Div(
        NotStr(INDEX_BODY),
        style="background:#04202e; min-height:100vh; padding:1px 0;",
    )


if __name__ == "__main__":
    serve()
