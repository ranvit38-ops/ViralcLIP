import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import auth, clips, projects
from .db import init_db

log = logging.getLogger("viralclip")

app = FastAPI(title="ViralClip API")
init_db()
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(clips.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    # Log the details server-side; never leak paths/tracebacks to the client.
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


@app.get("/api/health")
def health():
    return {"ok": True}
