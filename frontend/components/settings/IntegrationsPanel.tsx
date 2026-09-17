"use client";

import { useQuery } from "@tanstack/react-query";

import { SectionCard } from "@/components/shared";
import { Badge } from "@/components/ui";
import { INTEGRATION_LABELS } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { settingsApi } from "@/services/api";
import { INTEGRATION_KEYS } from "@/types/api";

/** ADMIN-only panel showing the configured/not-configured status of every external integration. */
export function IntegrationsPanel() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: queryKeys.settings.integrations,
    queryFn: () => settingsApi.integrations(),
  });

  return (
    <SectionCard
      title="Статус интеграций"
      description="Секретные ключи задаются только в файле .env на сервере — в панели их нет и они здесь не редактируются."
      loading={isLoading}
      error={error}
      onRetry={refetch}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {INTEGRATION_KEYS.map((key) => {
          const status = data?.[key];
          return (
            <div key={key} className="rounded-lg border border-slate-200 p-3">
              <div className="flex items-start justify-between gap-2">
                <span className="font-medium text-slate-900">{INTEGRATION_LABELS[key]}</span>
                <Badge tone={status?.configured ? "green" : "red"}>
                  {status?.configured ? "Настроено" : "Не настроено"}
                </Badge>
              </div>
              {status?.provider ? <p className="mt-1 text-sm text-slate-600">{status.provider}</p> : null}
              {status?.details ? <p className="mt-1 text-sm text-slate-500">{status.details}</p> : null}
            </div>
          );
        })}
      </div>
    </SectionCard>
  );
}
