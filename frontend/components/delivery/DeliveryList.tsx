"use client";

import Link from "next/link";

import { DeliveryRowActions } from "@/components/delivery/DeliveryRowActions";
import { CustomerLink, PhoneLink } from "@/components/shared/CustomerLink";
import { GeocodeStatusBadge, DeliveryStatusBadge } from "@/components/shared/StatusBadges";
import { MoneyText } from "@/components/shared/MoneyText";
import { TableSkeleton } from "@/components/shared/Skeletons";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { IconDelivery } from "@/components/ui/icons";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import { formatOrderNumber, formatTime } from "@/lib/format";
import type { DeliveryListItem } from "@/types/api";

export interface DeliveryListProps {
  deliveries: readonly DeliveryListItem[] | undefined;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  mapCenter: { lat: number; lng: number };
}

function dueAmount(delivery: DeliveryListItem): number {
  return delivery.order.total_amount - delivery.order.paid_amount;
}

function AddressCell({ delivery }: { delivery: DeliveryListItem }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="line-clamp-2 text-slate-900" title={delivery.address_formatted ?? delivery.address_raw}>
        {delivery.address_formatted ?? delivery.address_raw}
      </span>
      <GeocodeStatusBadge status={delivery.geocode_status} />
    </div>
  );
}

/** Deliveries for the selected date: desktop table + stacked cards below `md` (07 §5). */
export function DeliveryList({ deliveries, loading = false, error, onRetry, mapCenter }: DeliveryListProps) {
  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  if (loading && !deliveries?.length) return <TableSkeleton rows={5} columns={8} />;
  if (!deliveries || deliveries.length === 0) {
    return (
      <EmptyState
        icon={<IconDelivery className="size-6" />}
        title="Доставок на эту дату нет"
        description="Выберите другую дату или проверьте, что заказы подтверждены."
      />
    );
  }

  return (
    <div aria-busy={loading || undefined}>
      {/* Desktop / tablet */}
      <Table caption="Доставки" containerClassName="hidden xl:block">
        <THead>
          <tr>
            <TH>Заказ</TH>
            <TH>Время</TH>
            <TH>Адрес</TH>
            <TH>Получатель</TH>
            <TH>Комментарий курьеру</TH>
            <TH>Статус</TH>
            <TH align="right">К оплате</TH>
            <TH>Действия</TH>
          </tr>
        </THead>
        <TBody>
          {deliveries.map((delivery) => (
            <TR key={delivery.id}>
              <TD>
                <Link
                  href={`/orders/${delivery.order_id}`}
                  className="font-semibold text-brand-700 tabular-nums hover:underline"
                >
                  {formatOrderNumber(delivery.order_id)}
                </Link>
                <div className="mt-1">
                  <CustomerLink customer={delivery.order.customer} />
                </div>
              </TD>
              <TD className="whitespace-nowrap tabular-nums">{formatTime(delivery.order.delivery_time)}</TD>
              <TD className="max-w-64 min-w-48">
                <AddressCell delivery={delivery} />
              </TD>
              <TD className="min-w-36">
                <div className="flex flex-col gap-0.5">
                  <span>{delivery.recipient_name || <span className="text-slate-400">—</span>}</span>
                  <PhoneLink phone={delivery.recipient_phone} />
                </div>
              </TD>
              <TD className="max-w-48 min-w-32">
                {delivery.courier_comment ? (
                  <span className="line-clamp-2 text-slate-600" title={delivery.courier_comment}>
                    {delivery.courier_comment}
                  </span>
                ) : (
                  <span className="text-slate-400">—</span>
                )}
              </TD>
              <TD>
                <DeliveryStatusBadge status={delivery.status} />
              </TD>
              <TD align="right">
                <MoneyText value={dueAmount(delivery)} strong tone="auto" />
              </TD>
              <TD className="min-w-72">
                <DeliveryRowActions delivery={delivery} mapCenter={mapCenter} />
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>

      {/* Mobile / tablet: stacked cards */}
      <ul className="flex flex-col gap-3 xl:hidden" aria-label="Доставки">
        {deliveries.map((delivery) => (
          <li key={delivery.id} className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <Link
                  href={`/orders/${delivery.order_id}`}
                  className="font-semibold text-brand-700 tabular-nums hover:underline"
                >
                  {formatOrderNumber(delivery.order_id)}
                </Link>
                <span className="ml-2 text-sm text-slate-500 tabular-nums">
                  {formatTime(delivery.order.delivery_time)}
                </span>
                <div className="mt-1">
                  <CustomerLink customer={delivery.order.customer} />
                </div>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1">
                <DeliveryStatusBadge status={delivery.status} />
                <MoneyText value={dueAmount(delivery)} strong tone="auto" />
              </div>
            </div>

            <AddressCell delivery={delivery} />

            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-slate-700">
              <span>{delivery.recipient_name || "Получатель не указан"}</span>
              <PhoneLink phone={delivery.recipient_phone} />
            </div>

            {delivery.courier_comment ? (
              <p className="text-sm text-slate-500 italic">«{delivery.courier_comment}»</p>
            ) : null}

            <DeliveryRowActions delivery={delivery} mapCenter={mapCenter} />
          </li>
        ))}
      </ul>
    </div>
  );
}
