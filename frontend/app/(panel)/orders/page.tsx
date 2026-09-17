"use client";

import Link from "next/link";
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { OrderStatusMultiSelect } from "@/components/orders/OrderStatusMultiSelect";
import { FilterBar, FilterBarItem } from "@/components/shared/FilterBar";
import { OrderTable } from "@/components/shared/OrderTable";
import { PageSuspense } from "@/components/shared/Skeletons";
import { Button } from "@/components/ui/Button";
import { DateInput } from "@/components/ui/DateInput";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Pagination } from "@/components/ui/Pagination";
import { Select } from "@/components/ui/Select";
import { IconPlus } from "@/components/ui/icons";
import { useAuth } from "@/lib/auth";
import { addDays, businessToday } from "@/lib/format";
import { DELIVERY_TYPE_LABELS, optionsOf, PAYMENT_STATUS_LABELS } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { defineUrlState, urlParam, useUrlState } from "@/lib/url-state";
import { ordersApi } from "@/services/api";
import { DELIVERY_TYPES, ORDER_SORTS, ORDER_STATUSES, PAYMENT_STATUSES, type OrderListParams, type OrderSort } from "@/types/api";

const PAGE_SIZE = 20;

const SORT_LABELS: Record<OrderSort, string> = {
  "-created_at": "Сначала новые",
  delivery_date: "Дата доставки ↑",
  "-delivery_date": "Дата доставки ↓",
  total_amount: "Сумма",
};

const ORDER_FILTERS = defineUrlState({
  status: urlParam.enumArray(ORDER_STATUSES),
  payment_status: urlParam.enum(PAYMENT_STATUSES),
  delivery_type: urlParam.enum(DELIVERY_TYPES),
  customer_id: urlParam.id(),
  date_from: urlParam.date(),
  date_to: urlParam.date(),
  search: urlParam.string(),
  sort: urlParam.enum(ORDER_SORTS, "-created_at"),
  page: urlParam.page(),
});

const ORDER_FILTER_OPTIONS = {
  parse: ORDER_FILTERS.parse,
  serialize: ORDER_FILTERS.serialize,
  nonFilterKeys: ["sort"] as const,
};

function OrdersPageContent() {
  const { isAdmin } = useAuth();
  const { state, setState, setPage, reset, activeCount } = useUrlState(ORDER_FILTERS.defaults, ORDER_FILTER_OPTIONS);

  const params: OrderListParams = useMemo(
    () => ({
      status: state.status.length ? state.status : undefined,
      payment_status: state.payment_status || undefined,
      delivery_type: state.delivery_type || undefined,
      customer_id: state.customer_id ?? undefined,
      date_from: state.date_from || undefined,
      date_to: state.date_to || undefined,
      search: state.search || undefined,
      sort: state.sort,
      page: state.page,
      page_size: PAGE_SIZE,
    }),
    [state],
  );

  const query = useQuery({
    queryKey: queryKeys.orders.list(params),
    queryFn: () => ordersApi.list(params),
    placeholderData: (previous) => previous,
  });

  const today = businessToday();
  const tomorrow = addDays(today, 1);
  const isTodayRange = state.date_from === today && state.date_to === today;
  const isTomorrowRange = state.date_from === tomorrow && state.date_to === tomorrow;

  return (
    <>
      <PageHeader
        title="Заказы"
        description="Список заказов с фильтрами по дате, статусу, оплате и типу получения"
        actions={
          isAdmin ? (
            <Link href="/orders/new">
              <Button leftIcon={<IconPlus className="size-4" />}>Новый заказ</Button>
            </Link>
          ) : undefined
        }
      />

      <FilterBar
        columns={4}
        activeCount={activeCount}
        onReset={reset}
        search={
          <Input
            label="Поиск"
            placeholder="Номер заказа, имя или телефон"
            value={state.search}
            onChange={(event) => setState({ search: event.target.value })}
          />
        }
      >
        <FilterBarItem wide full>
          <span className="mb-1.5 block text-sm font-medium text-slate-700">Статус</span>
          <OrderStatusMultiSelect value={state.status} onChange={(status) => setState({ status })} />
        </FilterBarItem>

        <FilterBarItem>
          <DateInput
            label="Дата с"
            value={state.date_from}
            max={state.date_to || undefined}
            onValueChange={(date_from) => setState({ date_from })}
          />
        </FilterBarItem>
        <FilterBarItem>
          <DateInput
            label="Дата по"
            value={state.date_to}
            min={state.date_from || undefined}
            onValueChange={(date_to) => setState({ date_to })}
          />
        </FilterBarItem>
        <FilterBarItem>
          <span className="mb-1.5 block text-sm font-medium text-slate-700">Быстрый выбор</span>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant={isTodayRange ? "primary" : "outline"}
              onClick={() => setState({ date_from: today, date_to: today })}
            >
              Сегодня
            </Button>
            <Button
              type="button"
              size="sm"
              variant={isTomorrowRange ? "primary" : "outline"}
              onClick={() => setState({ date_from: tomorrow, date_to: tomorrow })}
            >
              Завтра
            </Button>
          </div>
        </FilterBarItem>

        <FilterBarItem>
          <Select
            label="Оплата"
            placeholder="Все"
            value={state.payment_status}
            onChange={(event) => setState({ payment_status: event.target.value as typeof state.payment_status })}
            options={optionsOf(PAYMENT_STATUS_LABELS)}
          />
        </FilterBarItem>
        <FilterBarItem>
          <Select
            label="Получение"
            placeholder="Все"
            value={state.delivery_type}
            onChange={(event) => setState({ delivery_type: event.target.value as typeof state.delivery_type })}
            options={optionsOf(DELIVERY_TYPE_LABELS)}
          />
        </FilterBarItem>
        <FilterBarItem>
          <Input
            label="ID клиента"
            type="number"
            min={1}
            value={state.customer_id ?? ""}
            onChange={(event) => setState({ customer_id: event.target.value ? Number(event.target.value) : null })}
          />
        </FilterBarItem>
        <FilterBarItem>
          <Select
            label="Сортировка"
            value={state.sort}
            onChange={(event) => setState({ sort: event.target.value as OrderSort })}
            options={ORDER_SORTS.map((value) => ({ value, label: SORT_LABELS[value] }))}
          />
        </FilterBarItem>
      </FilterBar>

      <div className="mt-4 overflow-hidden rounded-xl border border-slate-200 bg-white">
        <OrderTable
          orders={query.data?.items}
          loading={query.isFetching}
          error={query.error}
          onRetry={() => query.refetch()}
        />
        {query.data ? (
          <Pagination page={state.page} pageSize={PAGE_SIZE} total={query.data.total} onPageChange={setPage} disabled={query.isFetching} />
        ) : null}
      </div>
    </>
  );
}

export default function OrdersPage() {
  return (
    <PageSuspense>
      <OrdersPageContent />
    </PageSuspense>
  );
}
