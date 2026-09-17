"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import type { ReactNode } from "react";

import { RevenueTrendChart } from "@/components/dashboard/RevenueTrendChart";
import { OrderTable } from "@/components/shared/OrderTable";
import { SectionCard } from "@/components/shared/SectionCard";
import { Card } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/ErrorState";
import {
  IconAlert,
  IconConversations,
  IconCustomers,
  IconDelivery,
  IconFinance,
  IconOrders,
} from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatCard, type StatCardTone } from "@/components/ui/StatCard";
import { addDays, businessToday, formatMoney, formatNumber } from "@/lib/format";
import { queryKeys } from "@/lib/query";
import { statisticsApi } from "@/services/api";

const REFETCH_INTERVAL_MS = 60_000;

/** 14 calendar days ending today (business timezone), for the mini revenue chart. */
function last14DaysRange(): { date_from: string; date_to: string } {
  const date_to = businessToday();
  return { date_from: addDays(date_to, -13), date_to };
}

interface DashboardStat {
  key: string;
  label: string;
  value: string;
  icon: ReactNode;
  tone: StatCardTone;
  href?: string;
}

export default function DashboardPage() {
  const dashboardQuery = useQuery({
    queryKey: queryKeys.statistics.dashboard,
    queryFn: () => statisticsApi.dashboard(),
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  const timeseriesRange = last14DaysRange();
  const timeseriesParams = { date_from: timeseriesRange.date_from, date_to: timeseriesRange.date_to };
  const timeseriesQuery = useQuery({
    queryKey: queryKeys.statistics.timeseries(timeseriesParams),
    queryFn: () => statisticsApi.timeseries(timeseriesParams),
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  const dashboard = dashboardQuery.data;
  const loadingStats = dashboardQuery.isLoading;

  const stats: DashboardStat[] = [
    {
      key: "orders_today",
      label: "Заказы сегодня",
      value: formatNumber(dashboard?.orders_today ?? 0, 0),
      icon: <IconOrders className="size-5" />,
      tone: "brand",
    },
    {
      key: "revenue_today",
      label: "Выручка сегодня",
      value: formatMoney(dashboard?.revenue_today ?? 0),
      icon: <IconFinance className="size-5" />,
      tone: "green",
    },
    {
      key: "unpaid_orders_count",
      label: "Неоплаченные заказы",
      value: formatNumber(dashboard?.unpaid_orders_count ?? 0, 0),
      icon: <IconAlert className="size-5" />,
      tone: "red",
    },
    {
      key: "new_customers_today",
      label: "Новые клиенты",
      value: formatNumber(dashboard?.new_customers_today ?? 0, 0),
      icon: <IconCustomers className="size-5" />,
      tone: "blue",
    },
    {
      key: "regular_customers_today",
      label: "Постоянные клиенты",
      value: formatNumber(dashboard?.regular_customers_today ?? 0, 0),
      icon: <IconCustomers className="size-5" />,
      tone: "green",
    },
    {
      key: "delivery_orders_today",
      label: "Заказы с доставкой",
      value: formatNumber(dashboard?.delivery_orders_today ?? 0, 0),
      icon: <IconDelivery className="size-5" />,
      tone: "blue",
    },
    {
      key: "pickup_orders_today",
      label: "Заказы на самовывоз",
      value: formatNumber(dashboard?.pickup_orders_today ?? 0, 0),
      icon: <IconDelivery className="size-5" />,
      tone: "amber",
    },
    {
      key: "waiting_confirmation_count",
      label: "Ждут подтверждения",
      value: formatNumber(dashboard?.waiting_confirmation_count ?? 0, 0),
      icon: <IconAlert className="size-5" />,
      tone: "amber",
      href: "/orders?status=WAITING_CONFIRMATION",
    },
    {
      key: "conversations_needing_attention",
      label: "Диалоги требуют внимания",
      value: formatNumber(dashboard?.conversations_needing_attention ?? 0, 0),
      icon: <IconConversations className="size-5" />,
      tone: "red",
      href: "/conversations?needs_attention=true",
    },
  ];

  if (dashboardQuery.isError && !dashboard) {
    return (
      <>
        <PageHeader title="Dashboard" description="Сводка за сегодня: заказы, выручка, клиенты, доставка" />
        <Card>
          <ErrorState error={dashboardQuery.error} onRetry={() => dashboardQuery.refetch()} />
        </Card>
      </>
    );
  }

  return (
    <>
      <PageHeader title="Dashboard" description="Сводка за сегодня: заказы, выручка, клиенты, доставка" />
      <div className="flex flex-col gap-6">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {stats.map((stat) => (
            <StatCard
              key={stat.key}
              label={stat.label}
              value={stat.value}
              icon={stat.icon}
              tone={stat.tone}
              href={stat.href}
              loading={loadingStats}
            />
          ))}
        </div>

        <SectionCard
          title="Выручка за 14 дней"
          description="По дате доставки"
          loading={timeseriesQuery.isLoading}
          error={timeseriesQuery.error}
          onRetry={() => timeseriesQuery.refetch()}
          retrying={timeseriesQuery.isFetching}
          empty={!timeseriesQuery.isLoading && (timeseriesQuery.data?.length ?? 0) === 0}
          emptyTitle="Нет данных за период"
        >
          <RevenueTrendChart data={timeseriesQuery.data ?? []} />
        </SectionCard>

        <Card
          title="Последние заказы"
          description="10 последних оформленных заказов"
          actions={
            <Link href="/orders" className="text-sm font-medium text-brand-700 hover:underline">
              Все заказы
            </Link>
          }
          padded={false}
        >
          <OrderTable
            orders={dashboard?.recent_orders}
            compact
            loading={loadingStats}
            error={dashboardQuery.isError ? dashboardQuery.error : undefined}
            onRetry={() => dashboardQuery.refetch()}
            emptyTitle="Заказов пока нет"
            caption="Последние заказы"
          />
        </Card>
      </div>
    </>
  );
}
