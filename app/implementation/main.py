from fasthtml.common import fast_app, serve, Title, NotStr, Div
from dotenv import load_dotenv
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


@rt("/")
def get():
    return Title("Caribbean Fish Recall"), Div(
        NotStr(INDEX_BODY),
        style="background:#04202e; min-height:100vh; padding:1px 0;",
    )


if __name__ == "__main__":
    serve()
