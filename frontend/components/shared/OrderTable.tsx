"use client";

import clsx from "clsx";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { KeyboardEvent, MouseEvent, ReactNode } from "react";

import { CustomerLink, PhoneLink } from "@/components/shared/CustomerLink";
import { MoneyText } from "@/components/shared/MoneyText";
import { TableSkeleton } from "@/components/shared/Skeletons";
import { DeliveryTypeBadge, OrderStatusBadge, PaymentStatusBadge } from "@/components/shared/StatusBadges";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { IconOrders } from "@/components/ui/icons";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import { formatDate, formatDateAndTime, formatNumber, formatOrderNumber, formatTime } from "@/lib/format";
import type { OrderListItem } from "@/types/api";

export interface OrderTableProps {
  orders: readonly OrderListItem[] | undefined;
  /**
   * Fewer columns (№, клиент, товары, сумма, дата/время, оплата, статус) — for the dashboard,
   * customer card and other narrow places. Default false: all SPEC §17 columns.
   */
  compact?: boolean;
  /** Show the customer (and phone) columns (default true; false on the customer card). */
  showCustomer?: boolean;
  /** Skeleton while loading and there are no rows yet. */
  loading?: boolean;
  /** Error → ErrorState with `ApiError.detail`. */
  error?: unknown;
  onRetry?: () => void;
  emptyTitle?: ReactNode;
  emptyDescription?: ReactNode;
  emptyAction?: ReactNode;
  /** Accessible caption (visually hidden), default "Заказы". */
  caption?: string;
  /** Skeleton rows count (default 5). */
  skeletonRows?: number;
  className?: string;
}

const INTERACTIVE_SELECTOR = "a, button, input, select, textarea, label, [role='button']";

function orderHref(id: number): string {
  return `/orders/${id}`;
}

function Muted({ children }: { children: ReactNode }) {
  return <span className="text-slate-400">{children}</span>;
}

/**
 * Orders list per SPEC §17: № · клиент · телефон · товары · кол-во · сумма · дата · время · адрес ·
 * тип получения · оплата · статус · комментарий. Row click → `/orders/[id]` (Ctrl/⌘-click → new tab);
 * the order number is a real link for keyboard users. Below `md` rows become stacked cards.
 */
export function OrderTable({
  orders,
  compact = false,
  showCustomer = true,
  loading = false,
  error,
  onRetry,
  emptyTitle = "Заказов нет",
  emptyDescription,
  emptyAction,
  caption = "Заказы",
  skeletonRows = 5,
  className,
}: OrderTableProps) {
  const router = useRouter();

  if (error) {
    return <ErrorState error={error} onRetry={onRetry} className={className} />;
  }

  if (loading && !orders?.length) {
    return <TableSkeleton rows={skeletonRows} columns={compact ? 6 : 10} className={className} />;
  }

  if (!orders || orders.length === 0) {
    return (
      <EmptyState
        icon={<IconOrders className="size-6" />}
        title={emptyTitle}
        description={emptyDescription}
        action={emptyAction}
        className={className}
      />
    );
  }

  const openOrder = (id: number, event: MouseEvent<HTMLElement>) => {
    if (event.defaultPrevented || event.button !== 0) return;
    const target = event.target as HTMLElement;
    if (target.closest(INTERACTIVE_SELECTOR)) return;
    const selection = window.getSelection();
    if (selection && selection.toString().trim().length > 0) return;
    const href = orderHref(id);
    if (event.metaKey || event.ctrlKey) {
      window.open(href, "_blank", "noopener");
      return;
    }
    router.push(href);
  };

  const orderNumberLink = (order: OrderListItem, className?: string) => (
    <Link
      href={orderHref(order.id)}
      className={clsx(
        "font-semibold whitespace-nowrap text-brand-700 tabular-nums hover:underline focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600",
        className,
      )}
    >
      {formatOrderNumber(order.id)}
    </Link>
  );

  const amountCell = (order: OrderListItem) => (
    <div className="flex flex-col items-end">
      <MoneyText value={order.total_amount} strong />
      {!compact && order.payment_status === "PARTIALLY_PAID" ? (
        <span className="text-xs whitespace-nowrap text-slate-500">
          оплачено <MoneyText value={order.paid_amount} tone="muted" />
        </span>
      ) : null}
    </div>
  );

  const rowKeyDown = (order: OrderListItem, event: KeyboardEvent<HTMLElement>) => {
    // Rows are not focusable themselves; Enter on the focused number link navigates natively.
    if (event.key === "Enter" && event.target === event.currentTarget) router.push(orderHref(order.id));
  };

  return (
    <div className={className} aria-busy={loading || undefined}>
      {/* Desktop / tablet: table */}
      <Table caption={caption} containerClassName="hidden md:block">
        <THead>
          <tr>
            <TH>№</TH>
            {showCustomer ? <TH>Клиент</TH> : null}
            {showCustomer && !compact ? <TH>Телефон</TH> : null}
            <TH>Товары</TH>
            {!compact ? <TH align="right">Кол-во</TH> : null}
            <TH align="right">Сумма</TH>
            {compact ? (
              <TH>Дата и время</TH>
            ) : (
              <>
                <TH>Дата</TH>
                <TH>Время</TH>
                <TH>Адрес</TH>
                <TH>Получение</TH>
              </>
            )}
            <TH>Оплата</TH>
            <TH>Статус</TH>
            {!compact ? <TH>Комментарий</TH> : null}
          </tr>
        </THead>
        <TBody>
          {orders.map((order) => (
            <TR
              key={order.id}
              onClick={(event) => openOrder(order.id, event)}
              onKeyDown={(event) => rowKeyDown(order, event)}
              className="cursor-pointer transition-colors hover:bg-slate-50"
            >
              <TD>{orderNumberLink(order)}</TD>
              {showCustomer ? (
                <TD className="max-w-48">
                  <CustomerLink customer={order.customer} />
                  {compact && order.customer.phone ? (
                    <div className="text-xs text-slate-500 tabular-nums">{order.customer.phone}</div>
                  ) : null}
                </TD>
              ) : null}
              {showCustomer && !compact ? (
                <TD>
                  <PhoneLink phone={order.customer.phone} />
                </TD>
              ) : null}
              <TD className={compact ? "max-w-64 min-w-40" : "max-w-72 min-w-48"}>
                <span className="line-clamp-2" title={order.items_summary}>
                  {order.items_summary || <Muted>—</Muted>}
                </span>
              </TD>
              {!compact ? (
                <TD align="right" className="tabular-nums">
                  {formatNumber(order.items_count, 0)}
                </TD>
              ) : null}
              <TD align="right">{amountCell(order)}</TD>
              {compact ? (
                <TD className="whitespace-nowrap tabular-nums">
                  {formatDateAndTime(order.delivery_date, order.delivery_time)}
                </TD>
              ) : (
                <>
                  <TD className="whitespace-nowrap tabular-nums">{formatDate(order.delivery_date)}</TD>
                  <TD className="whitespace-nowrap tabular-nums">{formatTime(order.delivery_time)}</TD>
                  <TD className="max-w-56 min-w-40">
                    {order.delivery_address ? (
                      <span className="line-clamp-2" title={order.delivery_address}>
                        {order.delivery_address}
                      </span>
                    ) : (
                      <Muted>—</Muted>
                    )}
                  </TD>
                  <TD>
                    <DeliveryTypeBadge type={order.delivery_type} />
                  </TD>
                </>
              )}
              <TD>
                <PaymentStatusBadge status={order.payment_status} />
              </TD>
              <TD>
                <OrderStatusBadge status={order.status} />
              </TD>
              {!compact ? (
                <TD className="max-w-48 min-w-32">
                  {order.comment ? (
                    <span className="line-clamp-2 text-slate-600" title={order.comment}>
                      {order.comment}
                    </span>
                  ) : (
                    <Muted>—</Muted>
                  )}
                </TD>
              ) : null}
            </TR>
          ))}
        </TBody>
      </Table>

      {/* Mobile: stacked cards */}
      <ul className="divide-y divide-slate-100 md:hidden" aria-label={caption}>
        {orders.map((order) => (
          <li
            key={order.id}
            onClick={(event) => openOrder(order.id, event)}
            className="flex cursor-pointer flex-col gap-2 px-4 py-3 transition-colors hover:bg-slate-50"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 flex-col gap-0.5">
                {orderNumberLink(order, "text-base")}
                <span className="text-xs text-slate-500 tabular-nums">
                  {formatDateAndTime(order.delivery_date, order.delivery_time, "Дата не указана")}
                </span>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1">
                <OrderStatusBadge status={order.status} />
                <MoneyText value={order.total_amount} strong />
              </div>
            </div>

            {showCustomer ? (
              <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                <CustomerLink customer={order.customer} />
                {order.customer.phone ? <PhoneLink phone={order.customer.phone} className="text-sm" /> : null}
              </div>
            ) : null}

            <p className="line-clamp-2 text-sm text-slate-700">
              {order.items_summary || <Muted>Товары не указаны</Muted>}
              {!compact && order.items_count > 0 ? (
                <span className="text-slate-500"> · {formatNumber(order.items_count, 0)} шт.</span>
              ) : null}
            </p>

            {!compact && order.delivery_address ? (
              <p className="line-clamp-2 text-sm text-slate-500">{order.delivery_address}</p>
            ) : null}

            <div className="flex flex-wrap items-center gap-1.5">
              {order.delivery_type ? <DeliveryTypeBadge type={order.delivery_type} /> : null}
              <PaymentStatusBadge status={order.payment_status} />
            </div>

            {!compact && order.comment ? (
              <p className="line-clamp-2 text-sm text-slate-500 italic">«{order.comment}»</p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
