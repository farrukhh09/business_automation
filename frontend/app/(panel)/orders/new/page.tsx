"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { CustomerPicker } from "@/components/orders/CustomerPicker";
import { DeliveryAddressFields } from "@/components/orders/DeliveryAddressFields";
import { MissingFieldsPanel } from "@/components/orders/MissingFieldsPanel";
import { OrderItemsEditor, type OrderItemDraft } from "@/components/orders/OrderItemsEditor";
import { AccessDenied } from "@/components/layout/RoleGate";
import { SectionCard } from "@/components/shared/SectionCard";
import { SegmentedControl } from "@/components/shared/SegmentedControl";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { DateInput, TimeInput } from "@/components/ui/DateInput";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { useToast } from "@/components/ui/Toast";
import { useAuth } from "@/lib/auth";
import { businessToday } from "@/lib/format";
import { DELIVERY_TYPE_LABELS, optionsOf, PAYMENT_METHOD_LABELS } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { ordersApi } from "@/services/api";
import { isApiError } from "@/services/http";
import {
  DELIVERY_TYPES,
  type CustomerListItem,
  type DeliveryIn,
  type DeliveryType,
  type OrderCreate,
  type PaymentMethod,
} from "@/types/api";

export default function NewOrderPage() {
  const { isAdmin } = useAuth();

  return (
    <>
      <PageHeader title="Новый заказ" breadcrumbs={[{ label: "Заказы", href: "/orders" }, { label: "Новый заказ" }]} />
      {isAdmin ? (
        <NewOrderForm />
      ) : (
        <AccessDenied description="Создавать заказы вручную в админ-панели может только администратор." />
      )}
    </>
  );
}

function NewOrderForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const toast = useToast();

  const [customer, setCustomer] = useState<CustomerListItem | null>(null);
  const [items, setItems] = useState<OrderItemDraft[]>([]);
  const [deliveryType, setDeliveryType] = useState<DeliveryType>("PICKUP");
  const [deliveryDate, setDeliveryDate] = useState(businessToday());
  const [deliveryTime, setDeliveryTime] = useState("");
  const [comment, setComment] = useState("");
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod | "">("");
  const [delivery, setDelivery] = useState<DeliveryIn>({});
  const [confirm, setConfirm] = useState(true);
  const [missing, setMissing] = useState<string[]>([]);
  const [attempted, setAttempted] = useState(false);

  const createMutation = useMutation({
    mutationFn: (body: OrderCreate) => ordersApi.create(body),
    onSuccess: (order) => {
      toast.success("Заказ создан");
      queryClient.invalidateQueries({ queryKey: queryKeys.orders.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
      router.push(`/orders/${order.id}`);
    },
    onError: (error) => {
      if (isApiError(error) && error.code === "order_incomplete") {
        setMissing(error.body?.missing ?? []);
        toast.error("Не хватает данных", { description: "Заполните недостающие поля и сохраните ещё раз." });
        return;
      }
      toast.apiError(error);
    },
  });

  const valid = Boolean(customer) && items.length > 0 && Boolean(deliveryDate) && Boolean(deliveryTime);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    setAttempted(true);
    setMissing([]);
    if (!valid || !customer) return;
    const body: OrderCreate = {
      customer_id: customer.id,
      items: items.map((item) => ({
        product_id: item.product_id,
        quantity: item.quantity,
        comment: item.comment || undefined,
      })),
      delivery_type: deliveryType,
      delivery_date: deliveryDate,
      delivery_time: deliveryTime,
      comment: comment.trim() || undefined,
      payment_method: paymentMethod || undefined,
      delivery: deliveryType === "DELIVERY" ? delivery : undefined,
      confirm,
    };
    createMutation.mutate(body);
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <MissingFieldsPanel missing={missing} title="Заказ создан, но не может быть подтверждён — не хватает данных" />

      <SectionCard title="Клиент">
        <CustomerPicker
          selected={customer}
          onSelect={setCustomer}
          onClear={() => setCustomer(null)}
          error={attempted && !customer ? "Выберите или создайте клиента" : undefined}
        />
      </SectionCard>

      <SectionCard title="Товары">
        <OrderItemsEditor
          items={items}
          onChange={setItems}
          error={attempted && items.length === 0 ? "Добавьте хотя бы одну позицию" : undefined}
          disabled={createMutation.isPending}
        />
      </SectionCard>

      <SectionCard title="Получение">
        <div className="flex flex-col gap-4">
          <div>
            <span className="mb-1.5 block text-sm font-medium text-slate-700">Способ получения</span>
            <SegmentedControl
              ariaLabel="Способ получения"
              value={deliveryType}
              onChange={setDeliveryType}
              options={DELIVERY_TYPES.map((type) => ({ value: type, label: DELIVERY_TYPE_LABELS[type] }))}
              disabled={createMutation.isPending}
            />
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <DateInput
              label="Дата"
              required
              value={deliveryDate}
              min={businessToday()}
              onValueChange={setDeliveryDate}
              error={attempted && !deliveryDate ? "Укажите дату" : undefined}
              disabled={createMutation.isPending}
            />
            <TimeInput
              label="Время"
              required
              value={deliveryTime}
              onValueChange={setDeliveryTime}
              error={attempted && !deliveryTime ? "Укажите время" : undefined}
              disabled={createMutation.isPending}
            />
          </div>
          <Select
            label="Способ оплаты"
            placeholder="Не указан"
            value={paymentMethod}
            onChange={(event) => setPaymentMethod(event.target.value as PaymentMethod | "")}
            options={optionsOf(PAYMENT_METHOD_LABELS)}
            disabled={createMutation.isPending}
          />
          <Textarea
            label="Комментарий"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            rows={2}
            disabled={createMutation.isPending}
          />
        </div>
      </SectionCard>

      {deliveryType === "DELIVERY" ? (
        <SectionCard title="Адрес доставки">
          <DeliveryAddressFields value={delivery} onChange={setDelivery} disabled={createMutation.isPending} />
        </SectionCard>
      ) : null}

      <SectionCard title="Подтверждение">
        <Checkbox
          label="Подтвердить сразу"
          description="Если данных достаточно, заказ сразу перейдёт в статус «Подтверждён». Иначе останется черновиком до уточнения."
          checked={confirm}
          onChange={(event) => setConfirm(event.target.checked)}
          disabled={createMutation.isPending}
        />
      </SectionCard>

      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={() => router.push("/orders")} disabled={createMutation.isPending}>
          Отмена
        </Button>
        <Button type="submit" loading={createMutation.isPending}>
          Создать заказ
        </Button>
      </div>
    </form>
  );
}
