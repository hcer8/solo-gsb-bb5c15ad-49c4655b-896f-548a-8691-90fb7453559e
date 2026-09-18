"""FastAPI application: MIME multipart message inspector."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import mime_parser

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="MIME Multipart 报文检查器", version="1.0.0")


class ParseRequest(BaseModel):
    contentType: str = ""
    boundary: str = ""
    body: str = ""
    mode: str = "body"  # "body" | "message"


class EncodeRequest(BaseModel):
    tree: dict
    messageHeaders: str | None = None


@app.post("/api/parse")
def api_parse(req: ParseRequest):
    if req.mode not in ("body", "message"):
        raise HTTPException(status_code=422, detail="mode 必须是 'body' 或 'message'")
    return mime_parser.parse_message(req.contentType, req.boundary, req.body, req.mode)


@app.post("/api/encode")
def api_encode(req: EncodeRequest):
    if not isinstance(req.tree, dict) or not isinstance(req.tree.get("boundary"), str):
        raise HTTPException(status_code=400, detail="无效的部件树：缺少 boundary")
    message = mime_parser.encode_message(req.tree, req.messageHeaders or "")
    return {
        "ok": True,
        "message": message,
        "bytes": len(message.encode("utf-8")),
    }


@app.get("/api/health")
def api_health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
