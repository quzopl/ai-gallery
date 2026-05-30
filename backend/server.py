"""FastAPI backend dla AI Gallery."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

app = FastAPI(title="AI Gallery")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Mount frontend na końcu, html=True serwuje index.html dla "/"
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
