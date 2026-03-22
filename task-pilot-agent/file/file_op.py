from __future__ import annotations

import hashlib
import os
import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote

import aiofiles
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response, FileResponse
from config.config import agentSettings
from .file_table_op import (
	FileInfoOp,
	get_file_download_url,
	get_file_preview_url,
	FileDB,
)
from .file_type import FileUploadRequest, FileRequest, FileListRequest

file_router = APIRouter()
UPLOAD_DIR = agentSettings.core.upload_path


def get_file_id(request_id: str, file_name: str) -> str:
	return hashlib.md5((request_id + file_name).encode("utf-8")).hexdigest()


@file_router.post("/get_file")
async def get_file(file_info: FileRequest) -> Dict[str, Any]:
	file_record = await FileInfoOp.get_by_file_id(file_id=file_info.file_id)
	if not file_record:
		raise RuntimeError("file not found")

	return {
		"file_id": file_info.file_id or file_record.request_id,
		"file_name": file_record.filename,
		"file_size": file_record.file_size,
		"mime_type": file_record.mime_type,
	}


@file_router.post("/upload_file")
async def upload_file(
	file_info: FileUploadRequest
) -> Dict[str, Any]:
	info = await FileInfoOp.add_by_content(
		filename=file_info.file_name,
		content=file_info.content,
		file_id=file_info.file_id or get_file_id(file_info.request_id, file_info.file_name),
		description=file_info.description or "",
		request_id=file_info.request_id,
		encoding="utf-8",
	)
	preview_url = get_file_preview_url(request_id=info.request_id, file_name=info.filename)
	download_url = get_file_download_url(request_id=info.request_id, file_name=info.filename)
	return {
		"file_id": file_info.file_id,
		"file_ize": info.file_size,
		"domain_url": preview_url,
		"download_url": download_url,
	}


@file_router.post("/upload_file_data")
async def upload_file_data(file: UploadFile = File(...), request_id: str = Form(alias="requestId")):
    file.filename = unquote(file.filename)
    file_id = get_file_id(request_id, file.filename)
    file_info = await FileInfoOp.add_by_file(file=file, file_id=file_id, request_id=request_id)
    preview_url = get_file_preview_url(request_id=file_info.request_id, file_name=file_info.filename)
    download_url = get_file_download_url(request_id=file_info.request_id, file_name=file_info.filename)
    return JSONResponse(content={"download_url": download_url, "domain_url": preview_url, "fileSize": file_info.file_size})


@file_router.post("/upload_file_form")
async def upload_file_form(
	request_id: str = Form(...),
	file: UploadFile = File(...),
	description: Optional[str] = Form(None),
) -> Dict[str, Any]:
	if not file.filename:
		raise HTTPException(status_code=400, detail="filename missing")
	safe_name = os.path.basename(file.filename)
	target_path = UPLOAD_DIR / safe_name
	file_buffer = bytearray()
	async with aiofiles.open(target_path, "wb") as dst:
		while True:
			chunk = await file.read(1024 * 1024)
			if not chunk:
				break
			await dst.write(chunk)
			file_buffer.extend(chunk)
	await file.close()
	file_key = get_file_id(request_id, safe_name)
	info = await FileInfoOp.add_by_content(
		filename=safe_name,
		content=bytes(file_buffer),
		file_id=file_key,
		description=description or "",
		request_id=request_id,
		mime_type=file.content_type,
	)
	preview_url = get_file_preview_url(request_id=info.request_id, file_name=info.filename)
	download_url = get_file_download_url(request_id=info.request_id, file_name=info.filename)
	return {
		"file_id": info.file_id,
		"file_name": info.filename,
		"file_size": info.file_size,
		"mime_type": info.mime_type,
		"preview_url": preview_url,
		"download_url": download_url,
		"storage_path": str(target_path),
	}


@file_router.post("/get_file_list")
async def get_file_list(file_info: FileListRequest) -> Dict[str, Any]:
	if not file_info.filters:
		file_records = await FileInfoOp.get_by_request_id(file_info.request_id)
	else:
		file_ids = [f["file_id"] for f in file_info.filters if "file_id" in f]
		file_records = await FileInfoOp.get_by_file_ids(file_ids=file_ids)
	if not file_records:
		return {"results": [], "totalSize": 0}
	total_size = sum(record.file_size for record in file_records)
	file_entries = []
	for record in file_records:
		file_entries.append({
			"requestId": record.request_id,
			"fileName": record.filename,
			"fileSize": record.file_size,
			"mimeType": record.mime_type,
		})
	return {"results": file_entries, "totalSize": total_size}


@file_router.get("/download_file/{request_id}/{file_name}")
async def download_file(request_id: str, file_name: str, save_path: Optional[str] = None) -> str:
	file_key = get_file_id(request_id, file_name)
	print(f"file_id:{file_key} request_id:{request_id} file_name:{file_name}")
	file_info = await FileInfoOp.get_by_file_id(file_id=file_key)
	if not file_info or not file_info.content or not os.path.exists(file_info.file_path):
		file_info.file_path = await FileDB.save(file_info.filename, file_info.content, scope=file_info.request_id)
	return FileResponse(file_info.file_path, filename=os.path.basename(file_name))


@file_router.get("/preview_file/{request_id}/{file_name}")
async def preview_file(request_id: str, file_name: str) -> str:
	file_key = get_file_id(request_id, file_name)
	file_info = await FileInfoOp.get_by_file_id(file_id=file_key)
	if not file_info or not os.path.exists(file_info.file_path):
		file_info.file_path = await FileDB.save(file_info.filename, file_info.content, scope=file_info.request_id)

	disposition = "inline"
	if file_name.endswith(".md"):
		content_type = "text/markdown"
	else:
		content_type, _ = mimetypes.guess_type(file_name)
	if not content_type:
		content_type = "application/octet-stream"
		disposition = "attachment"

	encoded_file_name = quote(file_name)

	return FileResponse(
        file_info.file_path,
        filename=os.path.basename(file_name),
        media_type=content_type,
        headers={
            "Content-Disposition": f"{disposition}; filename=\"{encoded_file_name}\"; filename*=UTF-8''{encoded_file_name}",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        }
    )