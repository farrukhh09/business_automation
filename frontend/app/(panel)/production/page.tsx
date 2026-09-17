"use client";

import { useQuery } from "@tanstack/react-query";

import { ProductionTable } from "@/components/production/ProductionTable";
import { ProductionTextBlock } from "@/components/production/ProductionTextBlock";
import { PageSuspense, SectionCard } from "@/components/shared";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { DateInput } from "@/components/ui/DateInput";
import { PageHeader } from "@/components/ui/PageHeader";
import { addDays, businessToday } from "@/lib/format";
import { queryKeys } from "@/lib/query";
import { defineUrlState, urlParam, useUrlState } from "@/lib/url-state";
import { productionApi } from "@/services/api";

const PRODUCTION_URL_STATE = defineUrlState({
  date: urlParam.date(),
});

const PRODUCTION_URL_OPTIONS = {
  parse: PRODUCTION_URL_STATE.parse,
  serialize: PRODUCTION_URL_STATE.serialize,
  pageKey: null,
} as const;

function ProductionPageContent() {
  const { state, setState } = useUrlState(PRODUCTION_URL_STATE.defaults, PRODUCTION_URL_OPTIONS);
  const today = businessToday();
  const tomorrow = addDays(today, 1);
  const date = state.date || today;

  const query = useQuery({
    queryKey: queryKeys.production.byDate(date),
    queryFn: () => productionApi.get({ date }),
  });

  return (
    <>
      <PageHeader
        title="Производство"
        description="Сводка товаров из подтверждённых заказов на дату"
        actions={
          <div className="flex flex-wrap items-end gap-2">
            <DateInput label="Дата" value={date} onValueChange={(value) => setState({ date: value })} />
            <Button variant={date === today ? "primary" : "outline"} onClick={() => setState({ date: today })}>
              Сегодня
            </Button>
            <Button variant={date === tomorrow ? "primary" : "outline"} onClick={() => setState({ date: tomorrow })}>
              Завтра
            </Button>
          </div>
        }
      />

      <SectionCard
        title="Что печь"
        description={query.data ? `Заказов: ${query.data.orders_count}` : undefined}
        actions={query.data ? <Badge tone="blue">{query.data.orders_count} заказ(ов)</Badge> : undefined}
        loading={query.isLoading}
        error={query.error}
        onRetry={() => query.refetch()}
      >
        {query.data ? (
          <div className="flex flex-col gap-6">
            <ProductionTable items={query.data.items} />
            {query.data.items.length > 0 ? <ProductionTextBlock text={query.data.text} /> : null}
          </div>
        ) : null}
      </SectionCard>
    </>
  );
}

export default function ProductionPage() {
  return (
    <PageSuspense>
      <ProductionPageContent />
    </PageSuspense>
  );
}
