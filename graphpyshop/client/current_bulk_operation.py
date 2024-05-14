# Generated by ariadne-codegen
# Source: ./graphpyshop/queries

from typing import Any, Optional

from pydantic import Field

from .base_model import BaseModel
from .enums import BulkOperationErrorCode, BulkOperationStatus, BulkOperationType


class CurrentBulkOperation(BaseModel):
    current_bulk_operation: Optional[
        "CurrentBulkOperationCurrentBulkOperation"
    ] = Field(alias="currentBulkOperation")


class CurrentBulkOperationCurrentBulkOperation(BaseModel):
    id: str
    type: BulkOperationType
    status: BulkOperationStatus
    completed_at: Optional[Any] = Field(alias="completedAt")
    created_at: Any = Field(alias="createdAt")
    error_code: Optional[BulkOperationErrorCode] = Field(alias="errorCode")
    file_size: Optional[Any] = Field(alias="fileSize")
    object_count: Any = Field(alias="objectCount")
    partial_data_url: Optional[Any] = Field(alias="partialDataUrl")
    query: str
    root_object_count: Any = Field(alias="rootObjectCount")
    url: Optional[Any]


CurrentBulkOperation.model_rebuild()