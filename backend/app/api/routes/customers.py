"""Customers routes — docs/architecture/04-api.md §3 "Customers".

- ``GET   /api/customers``       STAFF  ``search``, ``customer_type``, ``sort``, pagination → ``Page[CustomerListItem]``
- ``GET   /api/customers/{id}``  STAFF  → ``CustomerDetail``
- ``POST  /api/customers``       STAFF  ``CustomerCreate`` → ``CustomerDetail`` 201
- ``PATCH /api/customers/{id}``  STAFF  ``CustomerUpdate`` → ``CustomerDetail``

Thin layer (01 §3): every rule lives in ``CustomerService``.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, status

from app.api.deps import DbSession, Pagination, StaffUser
from app.schemas.common import Page
from app.schemas.customer import CustomerCreate, CustomerDetail, CustomerListItem, CustomerUpdate
from app.services.customer_service import CustomerService

router = APIRouter(prefix="/customers", tags=["customers"])

CustomerId = Annotated[int, Path(ge=1, description="Идентификатор клиента")]
SearchQuery = Annotated[str | None, Query(max_length=128, description="Поиск по имени, username или телефону")]
CustomerTypeFilter = Annotated[Literal["new", "regular"] | None, Query(description="Новый / постоянный клиент")]
SortQuery = Annotated[
    str | None,
    Query(description="Сортировка: last_order_at/created_at/total_spent, префикс «-» = по убыванию"),
]


@router.get("", response_model=Page[CustomerListItem], summary="Список клиентов")
def list_customers(
    db: DbSession,
    user: StaffUser,
    pagination: Pagination,
    search: SearchQuery = None,
    customer_type: CustomerTypeFilter = None,
    sort: SortQuery = None,
) -> Page[CustomerListItem]:
    return CustomerService(db).list(
        search=search,
        customer_type=customer_type,
        sort=sort,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/{customer_id}", response_model=CustomerDetail, summary="Карточка клиента")
def get_customer(customer_id: CustomerId, db: DbSession, user: StaffUser) -> CustomerDetail:
    return CustomerService(db).detail(customer_id)


@router.post("", response_model=CustomerDetail, status_code=status.HTTP_201_CREATED, summary="Создать клиента")
def create_customer(payload: CustomerCreate, db: DbSession, user: StaffUser) -> CustomerDetail:
    service = CustomerService(db)
    customer = service.create(payload, user=user)
    return service.detail(customer.id)


@router.patch("/{customer_id}", response_model=CustomerDetail, summary="Изменить клиента")
def update_customer(
    customer_id: CustomerId, payload: CustomerUpdate, db: DbSession, user: StaffUser
) -> CustomerDetail:
    service = CustomerService(db)
    service.update(customer_id, payload, user=user)
    return service.detail(customer_id)
