"use client";

import { useQuery } from "@tanstack/react-query";

import { DonutChart } from "@/components/statistics/DonutChart";
import { OrdersTimeseriesChart } from "@/components/statistics/OrdersTimeseriesChart";
import { PageSuspense, PeriodPicker, SectionCard, usePeriodParams } from "@/components/shared";
import { Button, Card, ErrorState, PageHeader, StatCard } from "@/components/ui";
import { formatNumber } from "@/lib/format";
import { DATE_BASIS_LABELS } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { statisticsApi } from "@/services/api";
import type { DateBasis } from "@/types/api";

const CUSTOMER_NEW_COLOR = "#0284c7";
const CUSTOMER_REGULAR_COLOR = "#059669";
const DELIVERY_COLOR = "#0284c7";
const PICKUP_COLOR = "#ea580c";

function StatisticsPageContent() {
  const period = usePeriodParams();

  const statsQuery = useQuery({
    queryKey: queryKeys.statistics.summary(period.params),
    queryFn: () => statisticsApi.get(period.params),
    enabled: period.ready,
  });

  const timeseriesQuery = useQuery({
    queryKey: queryKeys.statistics.timeseries(period.timeseriesParams ?? {}),
    queryFn: () => statisticsApi.timeseries(period.timeseriesParams ?? undefined),
    enabled: period.ready && period.timeseriesParams !== null,
  });

  const customers = statsQuery.data?.customers;
  const delivery = statsQuery.data?.delivery;
  const loading = statsQuery.isLoading;

  // An empty period is often just the other date basis: orders confirmed today for the weekend are
  // "today" by confirmation and "Saturday" by delivery. Offer the switch instead of a bare zero.
  const otherBasis: DateBasis = period.value.date_basis === "delivery" ? "created" : "delivery";
  const emptyForBasis = statsQuery.data !== undefined && statsQuery.data.finance.orders_count === 0;
  const otherParams = { ...period.params, date_basis: otherBasis };
  const otherQuery = useQuery({
    queryKey: queryKeys.statistics.summary(otherParams),
    queryFn: () => statisticsApi.get(otherParams),
    enabled: period.ready && emptyForBasis,
  });
  const otherCount = otherQuery.data?.finance.orders_count ?? 0;

  return (
    <>
      <PageHeader title="Статистика" description="Клиенты, доставка и самовывоз за период" />
      <div className="flex flex-col gap-6">
        <Card>
          <PeriodPicker value={period.value} onChange={period.setValue} />
        </Card>

        {emptyForBasis && otherCount > 0 ? (
          <Card>
            <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-600">
              <p>
                {period.value.date_basis === "delivery"
                  ? `На эти даты доставок и выдач нет, но за период подтверждено заказов: ${formatNumber(otherCount, 0)}.`
                  : `За период заказов не подтверждали, но на эти даты назначено заказов: ${formatNumber(otherCount, 0)}.`}
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => period.setValue({ ...period.value, date_basis: otherBasis })}
              >
                {DATE_BASIS_LABELS[otherBasis]}
              </Button>
            </div>
          </Card>
        ) : null}

        {statsQuery.isError && !customers ? (
          <Card>
            <ErrorState error={statsQuery.error} onRetry={() => statsQuery.refetch()} />
          </Card>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <StatCard label="Новые клиенты" value={formatNumber(customers?.new_customers ?? 0, 0)} tone="blue" loading={loading} />
              <StatCard
                label="Постоянные клиенты"
                value={formatNumber(customers?.regular_customers ?? 0, 0)}
                tone="green"
                loading={loading}
              />
              <StatCard label="Всего клиентов" value={formatNumber(customers?.total_customers ?? 0, 0)} loading={loading} />
              <StatCard
                label="Заказы новых"
                value={formatNumber(customers?.new_customer_orders ?? 0, 0)}
                tone="blue"
                loading={loading}
              />
              <StatCard
                label="Заказы постоянных"
                value={formatNumber(customers?.regular_customer_orders ?? 0, 0)}
                tone="green"
                loading={loading}
              />
              <StatCard
                label="Зарегистрировано за период"
                value={formatNumber(customers?.customers_registered ?? 0, 0)}
                tone="brand"
                loading={loading}
              />
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <SectionCard
                title="Заказы: новые и постоянные клиенты"
                loading={loading}
                empty={!loading && (customers?.new_customer_orders ?? 0) + (customers?.regular_customer_orders ?? 0) === 0}
              >
                <DonutChart
                  totalLabel="заказов"
                  data={[
                    { key: "new", label: "Новые клиенты", value: customers?.new_customer_orders ?? 0, color: CUSTOMER_NEW_COLOR },
                    {
                      key: "regular",
                      label: "Постоянные клиенты",
                      value: customers?.regular_customer_orders ?? 0,
                      color: CUSTOMER_REGULAR_COLOR,
                    },
                  ]}
                />
              </SectionCard>

              <SectionCard
                title="Доставка и самовывоз"
                loading={loading}
                empty={!loading && (delivery?.delivery_orders ?? 0) + (delivery?.pickup_orders ?? 0) === 0}
              >
                <DonutChart
                  totalLabel="заказов"
                  data={[
                    { key: "delivery", label: "Доставка", value: delivery?.delivery_orders ?? 0, color: DELIVERY_COLOR },
                    { key: "pickup", label: "Самовывоз", value: delivery?.pickup_orders ?? 0, color: PICKUP_COLOR },
                  ]}
                />
              </SectionCard>
            </div>

            <SectionCard
              title="Количество заказов по дням"
              loading={timeseriesQuery.isLoading}
              error={timeseriesQuery.error}
              onRetry={() => timeseriesQuery.refetch()}
              retrying={timeseriesQuery.isFetching}
              empty={!timeseriesQuery.isLoading && (timeseriesQuery.data?.length ?? 0) === 0}
              emptyTitle="Нет данных за период"
            >
              <OrdersTimeseriesChart data={timeseriesQuery.data ?? []} />
            </SectionCard>
          </>
        )}
      </div>
    </>
  );
}

export default function StatisticsPage() {
  return (
    <PageSuspense>
      <StatisticsPageContent />
    </PageSuspense>
  );
}
