"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { MoneyText } from "@/components/shared/MoneyText";
import { SectionCard } from "@/components/shared/SectionCard";
import { PaymentStatusBadge } from "@/components/shared/StatusBadges";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import { useToast } from "@/components/ui/Toast";
import { formatDateTime } from "@/lib/format";
import {
  optionsOf,
  PAYMENT_KIND_LABELS,
  PAYMENT_KIND_TONES,
  PAYMENT_METHOD_LABELS,
  PAYMENT_STATUS_LABELS,
} from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { ordersApi } from "@/services/api";
import type { OrderDetail, PaymentKind, PaymentMethod, PaymentStatus } from "@/types/api";

export interface OrderPaymentPanelProps {
  order: OrderDetail;
}

/**
 * Payment section (03-business-rules.md §3): current status + progress bar, a manual status-change
 * control (`PARTIALLY_PAID` requires `paid_amount`), a "Добавить платёж/возврат" mini-form, and the
 * payments ledger. Available to both ADMIN and OPERATOR (backend allows both).
 */
export function OrderPaymentPanel({ order }: OrderPaymentPanelProps) {
  const queryClient = useQueryClient();
  const toast = useToast();

  const [statusTarget, setStatusTarget] = useState<PaymentStatus | "">("");
  const [partialAmount, setPartialAmount] = useState("");

  const [kind, setKind] = useState<PaymentKind>("PAYMENT");
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<PaymentMethod | "">("");
  const [note, setNote] = useState("");

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.orders.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
  };

  const statusMutation = useMutation({
    mutationFn: () =>
      ordersApi.update(order.id, {
        payment_status: statusTarget as PaymentStatus,
        paid_amount: statusTarget === "PARTIALLY_PAID" ? Number(partialAmount) : undefined,
      }),
    onSuccess: () => {
      toast.success("Статус оплаты изменён");
      setStatusTarget("");
      setPartialAmount("");
      invalidate();
    },
    onError: (error) => toast.apiError(error),
  });

  const paymentMutation = useMutation({
    mutationFn: () =>
      ordersApi.addPayment(order.id, {
        kind,
        amount: Number(amount),
        method: method || undefined,
        note: note.trim() || undefined,
      }),
    onSuccess: () => {
      toast.success(kind === "PAYMENT" ? "Платёж добавлен" : "Возврат зарегистрирован");
      setAmount("");
      setMethod("");
      setNote("");
      invalidate();
    },
    onError: (error) => toast.apiError(error),
  });

  const progress =
    order.total_amount > 0 ? Math.min(100, Math.max(0, Math.round((order.paid_amount / order.total_amount) * 100))) : 0;

  return (
    <SectionCard title="Оплата" description="Статус, платежи и возвраты по заказу">
      <div className="flex flex-col gap-5">
        <div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <PaymentStatusBadge status={order.payment_status} />
            <span className="text-sm text-slate-600">
              <MoneyText value={order.paid_amount} strong /> из <MoneyText value={order.total_amount} />
            </span>
          </div>
          <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-100" aria-hidden>
            <div className="h-full rounded-full bg-brand-600 transition-all" style={{ width: `${progress}%` }} />
          </div>
        </div>

        <form
          className="flex flex-wrap items-end gap-3 border-t border-slate-100 pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!statusTarget) return;
            if (statusTarget === "PARTIALLY_PAID" && !partialAmount) return;
            statusMutation.mutate();
          }}
        >
          <Select
            label="Изменить статус оплаты"
            placeholder="Выберите статус"
            value={statusTarget}
            onChange={(event) => setStatusTarget(event.target.value as PaymentStatus | "")}
            options={optionsOf(PAYMENT_STATUS_LABELS).filter((option) => option.value !== order.payment_status)}
            containerClassName="min-w-48"
          />
          {statusTarget === "PARTIALLY_PAID" ? (
            <Input
              label="Оплачено (сумма)"
              type="number"
              min={0}
              step="0.01"
              value={partialAmount}
              onChange={(event) => setPartialAmount(event.target.value)}
              required
              containerClassName="w-40"
            />
          ) : null}
          <Button type="submit" variant="outline" loading={statusMutation.isPending} disabled={!statusTarget}>
            Применить
          </Button>
        </form>

        <form
          className="flex flex-col gap-3 border-t border-slate-100 pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!amount || Number(amount) <= 0) return;
            paymentMutation.mutate();
          }}
        >
          <p className="text-sm font-medium text-slate-700">Добавить платёж / возврат</p>
          <div className="flex flex-wrap items-end gap-3">
            <Select
              label="Тип"
              value={kind}
              onChange={(event) => setKind(event.target.value as PaymentKind)}
              options={optionsOf(PAYMENT_KIND_LABELS)}
              containerClassName="w-36"
            />
            <Input
              label="Сумма"
              type="number"
              min={0}
              step="0.01"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              required
              containerClassName="w-36"
            />
            <Select
              label="Способ"
              placeholder="Не указан"
              value={method}
              onChange={(event) => setMethod(event.target.value as PaymentMethod | "")}
              options={optionsOf(PAYMENT_METHOD_LABELS)}
              containerClassName="w-40"
            />
            <Input
              label="Примечание"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              containerClassName="min-w-48 flex-1"
            />
            <Button type="submit" loading={paymentMutation.isPending}>
              Добавить
            </Button>
          </div>
        </form>

        <div className="border-t border-slate-100 pt-4">
          <p className="mb-2 text-sm font-medium text-slate-700">История платежей</p>
          {order.payments.length === 0 ? (
            <EmptyState title="Платежей пока нет" className="py-6" />
          ) : (
            <Table caption="Платежи">
              <THead>
                <tr>
                  <TH>Тип</TH>
                  <TH align="right">Сумма</TH>
                  <TH>Способ</TH>
                  <TH>Примечание</TH>
                  <TH>Дата</TH>
                </tr>
              </THead>
              <TBody>
                {order.payments.map((payment) => (
                  <TR key={payment.id}>
                    <TD>
                      <Badge tone={PAYMENT_KIND_TONES[payment.kind]}>{PAYMENT_KIND_LABELS[payment.kind]}</Badge>
                    </TD>
                    <TD align="right">
                      <MoneyText value={payment.amount} tone={payment.kind === "REFUND" ? "danger" : "success"} strong />
                    </TD>
                    <TD>{payment.method ? PAYMENT_METHOD_LABELS[payment.method] : "—"}</TD>
                    <TD className="max-w-56">
                      <span className="line-clamp-2">{payment.note || "—"}</span>
                    </TD>
                    <TD className="whitespace-nowrap">{formatDateTime(payment.paid_at)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </div>
      </div>
    </SectionCard>
  );
}
