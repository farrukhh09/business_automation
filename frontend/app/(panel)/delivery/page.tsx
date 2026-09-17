"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { DeliveryList } from "@/components/delivery/DeliveryList";
import { DeliverySummary } from "@/components/delivery/DeliverySummary";
import { DispatchSheetModal } from "@/components/delivery/DispatchSheetModal";
import { RoutePanel } from "@/components/delivery/RoutePanel";
import { PageSuspense, SectionCard } from "@/components/shared";
import { Button } from "@/components/ui/Button";
import { DateInput } from "@/components/ui/DateInput";
import { PageHeader } from "@/components/ui/PageHeader";
import { businessToday } from "@/lib/format";
import { queryKeys } from "@/lib/query";
import { defineUrlState, urlParam, useUrlState } from "@/lib/url-state";
import { deliveriesApi, settingsApi } from "@/services/api";

const KHUJAND_CENTER = { lat: 40.2842191, lng: 69.6191174 };

const DELIVERY_URL_STATE = defineUrlState({
  date: urlParam.date(),
});

const DELIVERY_URL_OPTIONS = {
  parse: DELIVERY_URL_STATE.parse,
  serialize: DELIVERY_URL_STATE.serialize,
  pageKey: null,
} as const;

function DeliveryPageContent() {
  const { state, setState } = useUrlState(DELIVERY_URL_STATE.defaults, DELIVERY_URL_OPTIONS);
  const today = businessToday();
  const date = state.date || today;
  const [sheetOpen, setSheetOpen] = useState(false);

  const settingsQuery = useQuery({ queryKey: queryKeys.settings.business, queryFn: () => settingsApi.get() });
  const deliveriesQuery = useQuery({
    queryKey: queryKeys.deliveries.list({ date }),
    queryFn: () => deliveriesApi.list({ date }),
  });

  const warehouse = settingsQuery.data?.warehouse;
  const mapCenter =
    warehouse?.latitude !== undefined && warehouse?.latitude !== null && warehouse?.longitude !== undefined && warehouse?.longitude !== null
      ? { lat: warehouse.latitude, lng: warehouse.longitude }
      : KHUJAND_CENTER;

  return (
    <>
      <PageHeader
        title="Доставка"
        description="Адреса, геокодирование, маршрут и передача в Maxim"
        actions={
          <div className="flex flex-wrap items-end gap-2">
            <DateInput label="Дата" value={date} onValueChange={(value) => setState({ date: value })} />
            <Button variant="outline" onClick={() => setState({ date: today })}>
              Сегодня
            </Button>
            <Button variant="outline" onClick={() => setSheetOpen(true)}>
              Лист для Maxim
            </Button>
          </div>
        }
      />

      <div className="flex flex-col gap-6">
        <DeliverySummary deliveries={deliveriesQuery.data} loading={deliveriesQuery.isLoading} />

        <SectionCard title="Доставки" description={`На ${date === today ? "сегодня" : date}`}>
          <DeliveryList
            deliveries={deliveriesQuery.data}
            loading={deliveriesQuery.isLoading}
            error={deliveriesQuery.error}
            onRetry={() => deliveriesQuery.refetch()}
            mapCenter={mapCenter}
          />
        </SectionCard>

        <RoutePanel date={date} deliveries={deliveriesQuery.data} warehouse={warehouse} />
      </div>

      <DispatchSheetModal date={date} open={sheetOpen} onClose={() => setSheetOpen(false)} />
    </>
  );
}

export default function DeliveryPage() {
  return (
    <PageSuspense>
      <DeliveryPageContent />
    </PageSuspense>
  );
}
