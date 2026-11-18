import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import auth, automation, chat, clips, importer, projects, tiktok, twitch, youtube
from .db import init_db

log = logging.getLogger("viralclip")

app = FastAPI(title="ViralClip API")
init_db()
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(clips.router)
app.include_router(youtube.router)
app.include_router(twitch.router)
app.include_router(tiktok.router)
app.include_router(automation.router)
app.include_router(importer.router)
app.include_router(chat.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    # Log the details server-side; never leak paths/tracebacks to the client.
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


@app.get("/api/health")
def health():
    return {"ok": True}
