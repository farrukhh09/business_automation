"""Orders routes — docs/architecture/04-api.md §5 "Orders".

- ``GET    /api/orders``                 STAFF  filters + pagination → ``Page[OrderListItem]``
- ``POST   /api/orders``                 ADMIN  ``OrderCreate`` → ``OrderDetail`` 201
- ``GET    /api/orders/{id}``            STAFF  → ``OrderDetail``
- ``PATCH  /api/orders/{id}``            STAFF  ``OrderUpdate`` → ``OrderDetail`` (field limits: ``OrderService``)
- ``POST   /api/orders/{id}/status``     STAFF  ``{status, comment?}`` → ``OrderDetail``
- ``POST   /api/orders/{id}/payments``   STAFF  ``{kind, amount, method?, note?}`` → ``OrderDetail``
- ``POST   /api/orders/{id}/cancel``     STAFF  ``{reason?}`` → ``OrderDetail``
- ``GET    /api/orders/{id}/events``     STAFF  → ``OrderEventOut[]``

Thin layer (01 §3): every rule — including which fields an OPERATOR may PATCH — lives in
``OrderService``, which is why routes pass the acting user straight through.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.api.deps import AdminUser, DbSession, Pagination, StaffUser
from app.models.enums import DeliveryType, OrderStatus, PaymentStatus
from app.schemas.common import Page
from app.schemas.order import (
    OrderCancelRequest,
    OrderCreate,
    OrderDetail,
    OrderEventOut,
    OrderListItem,
    OrderStatusChangeRequest,
    OrderUpdate,
    PaymentCreate,
)
from app.services.order_service import OrderService

router = APIRouter(prefix="/orders", tags=["orders"])

OrderId = Annotated[int, Path(ge=1, description="Номер заказа")]
StatusFilter = Annotated[
    list[OrderStatus] | None, Query(alias="status", description="Статус заказа (можно несколько)")
]
PaymentStatusFilter = Annotated[PaymentStatus | None, Query(description="Статус оплаты")]
DeliveryTypeFilter = Annotated[DeliveryType | None, Query(description="Тип получения")]
CustomerIdFilter = Annotated[int | None, Query(ge=1, description="Фильтр по клиенту")]
DeliveryDateFilter = Annotated[date | None, Query(description="Дата доставки")]
DateFromFilter = Annotated[date | None, Query(description="Дата доставки от (включительно)")]
DateToFilter = Annotated[date | None, Query(description="Дата доставки до (включительно)")]
SearchQuery = Annotated[
    str | None, Query(max_length=128, description="Номер заказа, имя, username или телефон клиента")
]
SortQuery = Annotated[
    str | None,
    Query(description="Сортировка: created_at/delivery_date/total_amount, префикс «-» = по убыванию"),
]


@router.get("", response_model=Page[OrderListItem], summary="Список заказов")
def list_orders(
    db: DbSession,
    user: StaffUser,
    pagination: Pagination,
    status_filter: StatusFilter = None,
    payment_status: PaymentStatusFilter = None,
    delivery_type: DeliveryTypeFilter = None,
    customer_id: CustomerIdFilter = None,
    delivery_date: DeliveryDateFilter = None,
    date_from: DateFromFilter = None,
    date_to: DateToFilter = None,
    search: SearchQuery = None,
    sort: SortQuery = None,
) -> Page[OrderListItem]:
    return OrderService(db).list_orders(
        statuses=status_filter,
        payment_status=payment_status,
        delivery_type=delivery_type,
        customer_id=customer_id,
        delivery_date=delivery_date,
        date_from=date_from,
        date_to=date_to,
        search=search,
        sort=sort,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post("", response_model=OrderDetail, status_code=status.HTTP_201_CREATED, summary="Создать заказ")
def create_order(payload: OrderCreate, db: DbSession, user: AdminUser) -> OrderDetail:
    service = OrderService(db)
    order = service.create_admin_order(payload, user)
    return service.detail(order, user)


@router.get("/{order_id}", response_model=OrderDetail, summary="Заказ")
def get_order(order_id: OrderId, db: DbSession, user: StaffUser) -> OrderDetail:
    return OrderService(db).get_detail(order_id, user)


@router.patch("/{order_id}", response_model=OrderDetail, summary="Изменить заказ")
def update_order(order_id: OrderId, payload: OrderUpdate, db: DbSession, user: StaffUser) -> OrderDetail:
    service = OrderService(db)
    order = service.update_order(order_id, payload, user)
    return service.detail(order, user)


@router.post("/{order_id}/status", response_model=OrderDetail, summary="Изменить статус заказа")
def change_order_status(
    order_id: OrderId, payload: OrderStatusChangeRequest, db: DbSession, user: StaffUser
) -> OrderDetail:
    service = OrderService(db)
    order = service.get(order_id)
    service.change_status(order, payload.status, user=user, comment=payload.comment)
    return service.detail(order, user)


@router.post("/{order_id}/payments", response_model=OrderDetail, summary="Зарегистрировать платёж или возврат")
def register_payment(order_id: OrderId, payload: PaymentCreate, db: DbSession, user: StaffUser) -> OrderDetail:
    service = OrderService(db)
    order = service.register_payment(order_id, payload, user=user)
    return service.detail(order, user)


@router.post("/{order_id}/cancel", response_model=OrderDetail, summary="Отменить заказ")
def cancel_order(order_id: OrderId, payload: OrderCancelRequest, db: DbSession, user: StaffUser) -> OrderDetail:
    service = OrderService(db)
    order = service.get(order_id)
    service.cancel(order, payload.reason, user=user)
    return service.detail(order, user)


@router.get("/{order_id}/events", response_model=list[OrderEventOut], summary="Журнал изменений заказа")
def list_order_events(order_id: OrderId, db: DbSession, user: StaffUser) -> list[OrderEventOut]:
    return OrderService(db).list_events(order_id)
