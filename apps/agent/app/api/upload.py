"""File upload endpoints — POST /v1/upload, GET /v1/uploads, DELETE /v1/uploads/{file_id}."""
from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.upload.parser import parse_file
from app.upload.storage import delete_file, list_files, store_file

router = APIRouter(prefix="/v1/uploads", tags=["uploads"])

_MAX_BYTES = 20 * 1024 * 1024  # 20 MB


@router.post("")
async def upload_file(request: Request, file: UploadFile):
    user_id: str = request.state.user_id
    data = await file.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise HTTPException(413, "File exceeds 20 MB limit")

    filename = file.filename or "upload"
    try:
        rows = parse_file(filename, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    file_id = await store_file(user_id, filename, rows)
    return JSONResponse({"file_id": file_id, "title": filename, "row_count": len(rows)})


@router.get("")
async def list_uploads(request: Request):
    user_id: str = request.state.user_id
    return await list_files(user_id)


@router.delete("/{file_id}")
async def delete_upload(request: Request, file_id: str):
    user_id: str = request.state.user_id
    removed = await delete_file(user_id, file_id)
    if not removed:
        raise HTTPException(404, "File not found")
    return {"deleted": file_id}
