import { MoneyText } from "@/components/shared/MoneyText";
import { SectionCard } from "@/components/shared/SectionCard";
import { StatCard } from "@/components/ui/StatCard";
import { formatQuantity, formatNumber } from "@/lib/format";
import type { DailyReportData } from "@/types/api";

export interface ReportSummaryCardsProps {
  data: DailyReportData;
}

/** Structured cards built from `DailyReportOut.data` (finance + customers + delivery + production). */
export function ReportSummaryCards({ data }: ReportSummaryCardsProps) {
  const { finance, customers, delivery, production } = data;
  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Заказов" value={formatNumber(finance.orders_count, 0)} tone="brand" />
        <StatCard label="Выручка" value={<MoneyText value={finance.revenue} strong />} tone="green" />
        <StatCard label="Оплачено" value={<MoneyText value={finance.paid_amount} strong tone="success" />} tone="green" />
        <StatCard label="Не оплачено" value={<MoneyText value={finance.unpaid_amount} strong tone="danger" />} tone="red" />
        <StatCard label="Новые клиенты" value={formatNumber(customers.new_customers, 0)} tone="blue" />
        <StatCard label="Постоянные клиенты" value={formatNumber(customers.regular_customers, 0)} tone="green" />
        <StatCard label="Доставка" value={formatNumber(delivery.delivery_orders, 0)} tone="blue" />
        <StatCard label="Самовывоз" value={formatNumber(delivery.pickup_orders, 0)} tone="amber" />
      </div>

      <SectionCard
        title="Производство"
        description={`Заказов на дату: ${formatNumber(production.orders_count, 0)}`}
        empty={production.items.length === 0}
        emptyTitle="Нет позиций на эту дату"
      >
        <ul className="divide-y divide-slate-100">
          {production.items.map((item) => (
            <li key={`${item.product_id ?? "x"}-${item.product_name}`} className="flex items-center justify-between gap-3 py-2 text-sm">
              <span className="text-slate-700">{item.product_name}</span>
              <span className="font-medium tabular-nums text-slate-900">{formatQuantity(item.quantity, item.unit)}</span>
            </li>
          ))}
        </ul>
      </SectionCard>
    </div>
  );
}
