# Generated by ariadne-codegen
# Source: ./graphpyshop/queries

from typing import Any, List, Optional

from pydantic import Field

from .base_model import BaseModel
from .enums import BulkOperationErrorCode, BulkOperationStatus, BulkOperationType


class BulkOperationRunMutation(BaseModel):
    bulk_operation_run_mutation: Optional[
        "BulkOperationRunMutationBulkOperationRunMutation"
    ] = Field(alias="bulkOperationRunMutation")


class BulkOperationRunMutationBulkOperationRunMutation(BaseModel):
    bulk_operation: Optional[
        "BulkOperationRunMutationBulkOperationRunMutationBulkOperation"
    ] = Field(alias="bulkOperation")
    user_errors: List[
        "BulkOperationRunMutationBulkOperationRunMutationUserErrors"
    ] = Field(alias="userErrors")


class BulkOperationRunMutationBulkOperationRunMutationBulkOperation(BaseModel):
    id: str
    status: BulkOperationStatus
    type: BulkOperationType
    query: str
    error_code: Optional[BulkOperationErrorCode] = Field(alias="errorCode")
    object_count: Any = Field(alias="objectCount")
    root_object_count: Any = Field(alias="rootObjectCount")
    file_size: Optional[Any] = Field(alias="fileSize")
    url: Optional[Any]
    partial_data_url: Optional[Any] = Field(alias="partialDataUrl")
    created_at: Any = Field(alias="createdAt")
    completed_at: Optional[Any] = Field(alias="completedAt")


class BulkOperationRunMutationBulkOperationRunMutationUserErrors(BaseModel):
    field: Optional[List[str]]
    message: str


BulkOperationRunMutation.model_rebuild()
BulkOperationRunMutationBulkOperationRunMutation.model_rebuild()