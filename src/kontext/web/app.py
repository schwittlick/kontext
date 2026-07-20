"""the resident search app (phase 3).

the cli pays ~20 s of model loading per query; this server loads bge-m3
(and, on first rerank, the cross-encoder) once at startup and keeps them
in memory, so queries are sub-second. the ui is plain server-rendered html
— a GET form, no build step, no javascript required — so any phone or
laptop on the lan can search by opening a page. bind 0.0.0.0, no auth: it
is a home network.

the original files are only ever read (FileResponse streams them); the
read-only dump is never touched.
"""

# no `from __future__ import annotations`: fastapi resolves route annotations
# at runtime, and the fastapi imports below are function-local (lazy). under
# stringized annotations `request: Request` would be unresolvable and fastapi
# would mistake `request` for a required query parameter.

import re
from contextlib import asynccontextmanager
from pathlib import Path

from markupsafe import Markup, escape

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_TEMPLATES = Path(__file__).parent / "templates"
_EXCERPT_CHARS = 360


def _connection_error(exc: Exception) -> bool:
    s = str(exc)
    return "onnect" in s or "efused" in s


def _highlight(text: str, query: str) -> Markup:
    """wrap query words in <mark>. everything is html-escaped first, so the
    result is safe to render unescaped even on ocr'd text full of angle
    brackets."""
    tokens = set(_TOKEN_RE.findall(query.casefold()))
    parts = []
    for piece in re.split(r"(\w+)", text, flags=re.UNICODE):
        if piece.casefold() in tokens:
            parts.append(f"<mark>{escape(piece)}</mark>")
        else:
            parts.append(str(escape(piece)))
    return Markup("".join(parts))


def _view(hit, query: str) -> dict:
    text = hit.text.strip()
    excerpt = text[:_EXCERPT_CHARS] + ("…" if len(text) > _EXCERPT_CHARS else "")
    return {
        "title": hit.title or "?",
        "author": hit.author or "unknown",
        "language": hit.language,
        "source_format": hit.source_format,
        "locator": hit.locator,
        "score": hit.score,
        "book_id": hit.book_id,
        "has_file": hit.path is not None,
        "excerpt": _highlight(excerpt, query),
    }


def create_app(db, qdrant: str = "http://localhost:6333", embedder=None, store=None):
    """build the fastapi app. `embedder`/`store` can be injected for tests;
    otherwise the real models load once at startup (lifespan)."""
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse
    from fastapi.templating import Jinja2Templates

    from kontext import catalog
    from kontext.search.embedder import EMBED_DIM, Embedder
    from kontext.search.query import search as run_query
    from kontext.search.store import VectorStore

    db = Path(db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.embedder is None:
            app.state.embedder = Embedder()
            _ = app.state.embedder.device      # load the model now, not on query 1
        if app.state.store is None:
            app.state.store = VectorStore(qdrant, dim=EMBED_DIM)
        yield

    app = FastAPI(title="kontext", lifespan=lifespan)
    app.state.embedder = embedder
    app.state.store = store
    templates = Jinja2Templates(directory=str(_TEMPLATES))

    def search_hits(text: str, limit: int, rerank: bool):
        # a fresh connection per request: cheap, and safe across the threadpool
        # that fastapi runs sync endpoints on. reads only.
        conn = catalog.connect(db)
        try:
            return run_query(conn, app.state.store, app.state.embedder,
                             text, limit=limit, rerank=rerank)
        finally:
            conn.close()

    @app.get("/api/search")
    def api_search(q: str, limit: int = 10, rerank: bool = False):
        if not q.strip():
            return {"query": q, "results": []}
        try:
            hits = search_hits(q, limit, rerank)
        except Exception as exc:
            if _connection_error(exc):
                raise HTTPException(503, "vector store unreachable — is qdrant running?")
            raise
        return {"query": q, "results": [
            {"title": h.title, "author": h.author, "language": h.language,
             "source_format": h.source_format, "locator": h.locator,
             "score": h.score, "book_id": h.book_id, "text": h.text,
             "download": f"/download/{h.book_id}" if h.path else None}
            for h in hits
        ]}

    @app.get("/")
    def home(request: Request, q: str = "", limit: int = 10, rerank: bool = False):
        searched = bool(q.strip())
        results, error = [], None
        if searched:
            try:
                results = [_view(h, q) for h in search_hits(q, limit, rerank)]
            except Exception as exc:
                if not _connection_error(exc):
                    raise
                error = "vector store unreachable — is qdrant running?"
        return templates.TemplateResponse(request, "index.html", {
            "q": q, "limit": limit, "rerank": rerank,
            "results": results, "searched": searched, "error": error,
        })

    @app.get("/download/{book_id}")
    def download(book_id: int):
        conn = catalog.connect(db)
        try:
            row = conn.execute(
                "SELECT path FROM book_files WHERE book_id=? AND role='primary' LIMIT 1",
                (book_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None or not Path(row[0]).exists():
            raise HTTPException(404, "file not found")
        path = Path(row[0])
        return FileResponse(path, filename=path.name)

    return app
