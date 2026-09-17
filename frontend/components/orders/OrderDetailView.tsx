"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { DeliveryAddressFields } from "@/components/orders/DeliveryAddressFields";
import { MissingFieldsPanel } from "@/components/orders/MissingFieldsPanel";
import { OrderItemsEditor, type OrderItemDraft } from "@/components/orders/OrderItemsEditor";
import { OrderJournal } from "@/components/orders/OrderJournal";
import { OrderPaymentPanel } from "@/components/orders/OrderPaymentPanel";
import { OrderStatusActions } from "@/components/orders/OrderStatusActions";
import { CustomerLink } from "@/components/shared/CustomerLink";
import { KeyValueList } from "@/components/shared/KeyValueList";
import { MoneyText } from "@/components/shared/MoneyText";
import { SectionCard } from "@/components/shared/SectionCard";
import { SegmentedControl } from "@/components/shared/SegmentedControl";
import { DeliveryTypeBadge, GeocodeStatusBadge, OrderStatusBadge, PaymentStatusBadge } from "@/components/shared/StatusBadges";
import { Button } from "@/components/ui/Button";
import { DateInput, TimeInput } from "@/components/ui/DateInput";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { PageSpinner } from "@/components/ui/Spinner";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import { Textarea } from "@/components/ui/Textarea";
import { useToast } from "@/components/ui/Toast";
import { useAuth } from "@/lib/auth";
import {
  formatCoordinates,
  formatDateAndTime,
  formatDateTime,
  formatOrderNumber,
  toTimeInputValue,
} from "@/lib/format";
import { DELIVERY_TYPE_LABELS, optionsOf, ORDER_SOURCE_LABELS, PAYMENT_METHOD_LABELS } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { ordersApi } from "@/services/api";
import { isApiError } from "@/services/http";
import {
  DELIVERY_TYPES,
  type DeliveryIn,
  type DeliveryType,
  type OrderDetail,
  type OrderUpdate,
  type PaymentMethod,
} from "@/types/api";

export interface OrderDetailViewProps {
  orderId: number;
}

/** Full `/orders/[id]` page content: header, status actions, items, delivery, payment and journal. */
export function OrderDetailView({ orderId }: OrderDetailViewProps) {
  const { isAdmin } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();

  const orderQuery = useQuery({
    queryKey: queryKeys.orders.detail(orderId),
    queryFn: () => ordersApi.get(orderId),
    enabled: Number.isFinite(orderId) && orderId > 0,
  });
  const order = orderQuery.data;

  const [editing, setEditing] = useState(false);
  const [items, setItems] = useState<OrderItemDraft[]>([]);
  const [deliveryType, setDeliveryType] = useState<DeliveryType>("PICKUP");
  const [deliveryDate, setDeliveryDate] = useState("");
  const [deliveryTime, setDeliveryTime] = useState("");
  const [comment, setComment] = useState("");
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod | "">("");
  const [delivery, setDelivery] = useState<DeliveryIn>({});
  const [missing, setMissing] = useState<string[] | null>(null);

  const startEdit = () => {
    if (!order) return;
    setItems(
      order.items.map((item) => ({
        product_id: item.product_id ?? 0,
        quantity: item.quantity,
        comment: item.comment ?? "",
        productLabel: item.product_name,
        unitPriceHint: item.unit_price,
      })),
    );
    setDeliveryType(order.delivery_type ?? "PICKUP");
    setDeliveryDate(order.delivery_date ?? "");
    setDeliveryTime(toTimeInputValue(order.delivery_time));
    setComment(order.comment ?? "");
    setPaymentMethod(order.payment_method ?? "");
    setDelivery(
      order.delivery
        ? {
            address_raw: order.delivery.address_raw,
            district: order.delivery.district,
            microdistrict: order.delivery.microdistrict,
            street: order.delivery.street,
            house: order.delivery.house,
            apartment: order.delivery.apartment,
            entrance: order.delivery.entrance,
            floor: order.delivery.floor,
            landmark: order.delivery.landmark,
            recipient_name: order.delivery.recipient_name,
            recipient_phone: order.delivery.recipient_phone,
            courier_comment: order.delivery.courier_comment,
          }
        : {},
    );
    setMissing(null);
    setEditing(true);
  };

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.orders.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
  };

  const updateMutation = useMutation({
    mutationFn: (body: OrderUpdate) => ordersApi.update(orderId, body),
    onSuccess: () => {
      toast.success("Заказ обновлён");
      setMissing(null);
      invalidate();
      setEditing(false);
    },
    onError: (error) => {
      if (isApiError(error) && error.code === "order_incomplete") {
        setMissing(error.body?.missing ?? []);
        toast.error("Не удалось сохранить", { description: "Проверьте недостающие поля." });
        return;
      }
      toast.apiError(error);
    },
  });

  const handleSave = (event: FormEvent) => {
    event.preventDefault();
    const body: OrderUpdate = {
      comment: comment.trim() || null,
      payment_method: paymentMethod || null,
      delivery: deliveryType === "DELIVERY" ? delivery : null,
    };
    if (isAdmin) {
      body.items = items.map((item) => ({
        product_id: item.product_id,
        quantity: item.quantity,
        comment: item.comment || undefined,
      }));
      body.delivery_type = deliveryType;
      body.delivery_date = deliveryDate;
      body.delivery_time = deliveryTime;
    }
    updateMutation.mutate(body);
  };

  if (orderQuery.isLoading) return <PageSpinner label="Загрузка заказа…" />;

  if (orderQuery.error || !order) {
    return (
      <SectionCard
        title={`Заказ №${orderId}`}
        error={orderQuery.error ?? new Error("Заказ не найден")}
        onRetry={() => orderQuery.refetch()}
      />
    );
  }

  const effectiveDeliveryType = editing ? deliveryType : order.delivery_type;

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={formatOrderNumber(order.id)}
        breadcrumbs={[{ label: "Заказы", href: "/orders" }, { label: formatOrderNumber(order.id) }]}
        description={`Создан ${formatDateTime(order.created_at)}${
          order.confirmed_at ? ` · Подтверждён ${formatDateTime(order.confirmed_at)}` : ""
        }`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <OrderStatusBadge status={order.status} />
            <PaymentStatusBadge status={order.payment_status} />
            {!editing ? (
              <Button variant="outline" onClick={startEdit}>
                Редактировать
              </Button>
            ) : null}
          </div>
        }
      />

      <MissingFieldsPanel missing={missing ?? order.missing_fields} />

      <SectionCard title="Клиент">
        <KeyValueList
          layout="rows"
          items={[
            { label: "Клиент", value: <CustomerLink customer={order.customer} /> },
            { label: "Телефон", value: order.customer.phone },
            { label: "Тип клиента", value: order.is_repeat_customer ? "Постоянный" : "Новый" },
            { label: "Источник заказа", value: ORDER_SOURCE_LABELS[order.source] },
            {
              label: "Диалог",
              value: order.conversation_id ? (
                <Link className="text-brand-700 hover:underline" href={`/conversations/${order.conversation_id}`}>
                  Открыть диалог
                </Link>
              ) : null,
            },
            { label: "Причина отмены", value: order.cancel_reason, hidden: order.status !== "CANCELLED" },
          ]}
        />
      </SectionCard>

      <OrderStatusActions order={order} />

      <form onSubmit={handleSave} className="flex flex-col gap-4">
        <SectionCard title="Товары">
          {editing && isAdmin ? (
            <OrderItemsEditor items={items} onChange={setItems} disabled={updateMutation.isPending} />
          ) : (
            <OrderItemsTable order={order} />
          )}
        </SectionCard>

        <SectionCard title="Получение и оплата">
          {editing ? (
            <div className="flex flex-col gap-4">
              <div>
                <span className="mb-1.5 block text-sm font-medium text-slate-700">Способ получения</span>
                {isAdmin ? (
                  <SegmentedControl
                    ariaLabel="Способ получения"
                    value={deliveryType}
                    onChange={setDeliveryType}
                    options={DELIVERY_TYPES.map((type) => ({ value: type, label: DELIVERY_TYPE_LABELS[type] }))}
                    disabled={updateMutation.isPending}
                  />
                ) : (
                  <DeliveryTypeBadge type={order.delivery_type} />
                )}
              </div>
              {isAdmin ? (
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <DateInput
                    label="Дата"
                    value={deliveryDate}
                    onValueChange={setDeliveryDate}
                    disabled={updateMutation.isPending}
                  />
                  <TimeInput
                    label="Время"
                    value={deliveryTime}
                    onValueChange={setDeliveryTime}
                    disabled={updateMutation.isPending}
                  />
                </div>
              ) : (
                <p className="text-sm text-slate-600">{formatDateAndTime(order.delivery_date, order.delivery_time)}</p>
              )}
              <Select
                label="Способ оплаты"
                placeholder="Не указан"
                value={paymentMethod}
                onChange={(event) => setPaymentMethod(event.target.value as PaymentMethod | "")}
                options={optionsOf(PAYMENT_METHOD_LABELS)}
                disabled={updateMutation.isPending}
              />
              <Textarea
                label="Комментарий"
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                rows={2}
                disabled={updateMutation.isPending}
              />
            </div>
          ) : (
            <KeyValueList
              layout="rows"
              items={[
                { label: "Способ получения", value: <DeliveryTypeBadge type={order.delivery_type} /> },
                { label: "Дата и время", value: formatDateAndTime(order.delivery_date, order.delivery_time) },
                {
                  label: "Способ оплаты",
                  value: order.payment_method ? PAYMENT_METHOD_LABELS[order.payment_method] : null,
                },
                { label: "Комментарий", value: order.comment },
              ]}
            />
          )}
        </SectionCard>

        {effectiveDeliveryType === "DELIVERY" ? (
          <SectionCard title="Адрес доставки">
            {editing ? (
              <DeliveryAddressFields value={delivery} onChange={setDelivery} disabled={updateMutation.isPending} />
            ) : (
              <DeliveryReadOnly order={order} />
            )}
          </SectionCard>
        ) : null}

        {editing ? (
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setEditing(false)} disabled={updateMutation.isPending}>
              Отмена
            </Button>
            <Button type="submit" loading={updateMutation.isPending}>
              Сохранить
            </Button>
          </div>
        ) : null}
      </form>

      <OrderPaymentPanel order={order} />

      <OrderJournal orderId={order.id} />
    </div>
  );
}

function OrderItemsTable({ order }: { order: OrderDetail }) {
  return (
    <Table caption="Товары в заказе">
      <THead>
        <tr>
          <TH>Товар</TH>
          <TH align="right">Кол-во</TH>
          <TH align="right">Цена</TH>
          <TH align="right">Сумма</TH>
          <TH>Комментарий</TH>
        </tr>
      </THead>
      <TBody>
        {order.items.map((item) => (
          <TR key={item.id}>
            <TD>{item.product_name}</TD>
            <TD align="right" className="tabular-nums">
              {item.quantity}
            </TD>
            <TD align="right">
              <MoneyText value={item.unit_price} />
            </TD>
            <TD align="right">
              <MoneyText value={item.total_price} strong />
            </TD>
            <TD className="max-w-56">{item.comment || "—"}</TD>
          </TR>
        ))}
        <TR>
          <TD colSpan={3} className="text-right font-medium text-slate-700">
            Итого
          </TD>
          <TD align="right">
            <MoneyText value={order.total_amount} strong />
          </TD>
          <TD />
        </TR>
      </TBody>
    </Table>
  );
}

function DeliveryReadOnly({ order }: { order: OrderDetail }) {
  const delivery = order.delivery;
  const lat = order.delivery_latitude;
  const lng = order.delivery_longitude;
  const mapUrl =
    lat !== null && lng !== null ? `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}#map=17/${lat}/${lng}` : null;

  return (
    <KeyValueList
      layout="rows"
      items={[
        { label: "Адрес", value: delivery?.address_formatted || delivery?.address_raw || order.delivery_address },
        {
          label: "Район / микрорайон",
          value: [delivery?.district, delivery?.microdistrict].filter(Boolean).join(" / "),
        },
        { label: "Геокодирование", value: <GeocodeStatusBadge status={delivery?.geocode_status} /> },
        {
          label: "Координаты",
          value: mapUrl ? (
            <a href={mapUrl} target="_blank" rel="noreferrer" className="text-brand-700 hover:underline">
              {formatCoordinates(lat, lng)}
            </a>
          ) : (
            formatCoordinates(lat, lng)
          ),
        },
        { label: "Получатель", value: delivery?.recipient_name },
        { label: "Телефон получателя", value: delivery?.recipient_phone },
        { label: "Комментарий курьеру", value: delivery?.courier_comment },
        {
          label: "Маршрут на дату",
          value: order.delivery_date ? (
            <Link className="text-brand-700 hover:underline" href={`/delivery?date=${order.delivery_date}`}>
              Открыть доставку
            </Link>
          ) : null,
        },
      ]}
    />
  );
}
