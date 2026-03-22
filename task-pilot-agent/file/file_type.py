from __future__ import annotations

import hashlib
from typing import List, Optional

from pydantic import BaseModel, Field, computed_field


class FileRequest(BaseModel):
	request_id: str = Field(alias="request_id", description="Request ID")
	file_name: str = Field(alias="file_name", description="文件名称")

	@computed_field
	def file_id(self) -> str:
		return get_file_id(self.request_id, self.file_name)


def get_file_id(request_id: str, file_name: str) -> str:
	return hashlib.md5((request_id + file_name).encode("utf-8")).hexdigest()


class FileListRequest(BaseModel):
	request_id: str = Field(alias="request_id", description="Request ID")
	filters: Optional[List[FileRequest]] = Field(default=None, description="过滤条件")
	page: int = 1
	page_size: int = Field(default=10, alias="pageSize", description="每页数量")


class FileUploadRequest(FileRequest):
	description: str | None = Field(
        default=None,
        description="上传文件摘要（可选）"
    )
	content: str = Field(description="上传文件内容")
