# Generated by ariadne-codegen
# Source: ./graphpyshop/queries

from typing import Any, List

from pydantic import Field

from .base_model import BaseModel


class ProductVariants(BaseModel):
    product_variants: "ProductVariantsProductVariants" = Field(alias="productVariants")


class ProductVariantsProductVariants(BaseModel):
    edges: List["ProductVariantsProductVariantsEdges"]


class ProductVariantsProductVariantsEdges(BaseModel):
    node: "ProductVariantsProductVariantsEdgesNode"


class ProductVariantsProductVariantsEdgesNode(BaseModel):
    id: str
    updated_at: Any = Field(alias="updatedAt")


ProductVariants.model_rebuild()
ProductVariantsProductVariants.model_rebuild()
ProductVariantsProductVariantsEdges.model_rebuild()