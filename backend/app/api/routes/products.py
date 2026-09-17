"""Products routes — docs/architecture/04-api.md §4 "Products".

- ``GET    /api/products``       STAFF  ``include_inactive``, ``search`` → ``ProductOut[]`` (without deleted)
- ``GET    /api/products/{id}``  STAFF  → ``ProductOut``
- ``POST   /api/products``       ADMIN  ``ProductCreate`` → ``ProductOut`` 201
- ``PATCH  /api/products/{id}``  ADMIN  ``ProductUpdate`` → ``ProductOut``
- ``DELETE /api/products/{id}``  ADMIN  soft delete (``deleted_at``, ``is_active=false``) → 204

Thin layer (01 §3): every rule lives in ``ProductService``.
"""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status

from app.api.deps import AdminUser, DbSession, StaffUser
from app.models.product import Product
from app.schemas.product import ProductCreate, ProductOut, ProductUpdate
from app.services.product_service import ProductService

router = APIRouter(prefix="/products", tags=["products"])

ProductId = Annotated[int, Path(ge=1, description="Идентификатор товара")]
IncludeInactive = Annotated[bool, Query(description="Показывать выключенные товары")]
SearchQuery = Annotated[str | None, Query(max_length=128, description="Поиск по названию и синонимам")]


@router.get("", response_model=list[ProductOut], summary="Список товаров")
def list_products(
    db: DbSession,
    user: StaffUser,
    include_inactive: IncludeInactive = False,
    search: SearchQuery = None,
) -> list[Product]:
    return ProductService(db).list(include_inactive=include_inactive, search=search)


@router.get("/{product_id}", response_model=ProductOut, summary="Товар")
def get_product(product_id: ProductId, db: DbSession, user: StaffUser) -> Product:
    return ProductService(db).get(product_id)


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED, summary="Создать товар")
def create_product(payload: ProductCreate, db: DbSession, user: AdminUser) -> Product:
    return ProductService(db).create(payload, user=user)


@router.patch("/{product_id}", response_model=ProductOut, summary="Изменить товар")
def update_product(product_id: ProductId, payload: ProductUpdate, db: DbSession, user: AdminUser) -> Product:
    return ProductService(db).update(product_id, payload, user=user)


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Удалить товар (мягкое удаление)",
)
def delete_product(product_id: ProductId, db: DbSession, user: AdminUser) -> Response:
    ProductService(db).delete(product_id, user=user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
