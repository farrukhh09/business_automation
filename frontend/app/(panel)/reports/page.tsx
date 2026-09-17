"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ReportHistoryList } from "@/components/reports/ReportHistoryList";
import { ReportSummaryCards } from "@/components/reports/ReportSummaryCards";
import { CopyButton } from "@/components/shared";
import { RoleGate } from "@/components/layout/RoleGate";
import { Button, Card, ConfirmDialog, DateInput, ErrorState, PageHeader, useToast } from "@/components/ui";
import { IconPrinter } from "@/components/ui/icons";
import { businessToday, formatDateLong, formatNumber } from "@/lib/format";
import { queryKeys } from "@/lib/query";
import { isApiError } from "@/services/http";
import { reportsApi, statisticsApi } from "@/services/api";

const HISTORY_LIMIT = 30;

export default function ReportsPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  // Opens on the day in progress (recomputed live by the backend); the evening job fixes the
  // final version at `daily_report_time`. Earlier days are the stored snapshots.
  const [selectedDate, setSelectedDate] = useState<string>(() => businessToday());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [regenerating, setRegenerating] = useState(false);

  const reportQuery = useQuery({
    queryKey: queryKeys.reports.daily(selectedDate || undefined),
    queryFn: () => reportsApi.daily(selectedDate ? { date: selectedDate } : undefined),
  });

  const historyQuery = useQuery({
    queryKey: queryKeys.reports.history(HISTORY_LIMIT),
    queryFn: () => reportsApi.history({ limit: HISTORY_LIMIT }),
  });

  const report = reportQuery.data;
  const effectiveDate = selectedDate || report?.date || "";

  // The report counts the orders handed out on that date (03 §4, delivery date). Orders confirmed
  // that day for later dates are easy to miss — say where they went instead of showing a bare zero.
  const confirmedParams = {
    period: "custom" as const,
    date_from: effectiveDate,
    date_to: effectiveDate,
    date_basis: "created" as const,
  };
  const confirmedQuery = useQuery({
    queryKey: queryKeys.statistics.summary(confirmedParams),
    queryFn: () => statisticsApi.get(confirmedParams),
    enabled: Boolean(effectiveDate) && report !== undefined && report.data.finance.orders_count === 0,
  });
  const confirmedCount = confirmedQuery.data?.finance.orders_count ?? 0;

  const handleRegenerate = async () => {
    if (!effectiveDate) return;
    setRegenerating(true);
    try {
      await reportsApi.generate({ date: effectiveDate });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.reports.dailyAll }),
        queryClient.invalidateQueries({ queryKey: queryKeys.reports.historyAll }),
      ]);
      toast.success("Отчёт пересобран");
      setConfirmOpen(false);
    } catch (error) {
      toast.error(isApiError(error) ? error.detail : "Не удалось пересобрать отчёт");
    } finally {
      setRegenerating(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Отчёты"
        description="Отчёт за день: заказы, которые выдаются или доставляются в эту дату, и что для них испечь"
        className="no-print"
      />
      <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
        <div className="flex min-w-0 flex-1 flex-col gap-6">
          <Card className="no-print">
            <div className="flex flex-wrap items-end gap-3">
              <DateInput
                label="Дата отчёта"
                value={effectiveDate}
                onValueChange={(value) => setSelectedDate(value)}
                containerClassName="w-48"
              />
              <RoleGate roles={["ADMIN"]}>
                <Button variant="outline" onClick={() => setConfirmOpen(true)} disabled={!effectiveDate || reportQuery.isLoading}>
                  Пересобрать
                </Button>
              </RoleGate>
            </div>
          </Card>

          {reportQuery.isError ? (
            <Card>
              <ErrorState error={reportQuery.error} onRetry={() => reportQuery.refetch()} />
            </Card>
          ) : (
            <>
              <div>
                <div className="no-print mb-3 flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-base font-semibold text-slate-900">
                    {effectiveDate ? `Отчёт за ${formatDateLong(effectiveDate)}` : "Отчёт"}
                  </h2>
                  <div className="flex gap-2">
                    <CopyButton text={() => report?.text ?? ""} disabled={!report} label="Копировать" />
                    <Button
                      variant="outline"
                      size="sm"
                      leftIcon={<IconPrinter className="size-4" />}
                      onClick={() => window.print()}
                      disabled={!report}
                    >
                      Печать
                    </Button>
                  </div>
                </div>
                <pre className="overflow-x-auto rounded-xl border border-slate-200 bg-white p-4 font-mono text-sm whitespace-pre-wrap text-slate-800 shadow-xs sm:p-6 print:rounded-none print:border-0 print:p-0 print:shadow-none">
                  {reportQuery.isLoading ? "Загрузка…" : (report?.text ?? "")}
                </pre>
                {report && report.data.finance.orders_count === 0 && confirmedCount > 0 ? (
                  <p className="no-print mt-3 text-sm text-slate-600">
                    В этот день подтверждено заказов: {formatNumber(confirmedCount, 0)} — на другие даты. Они войдут в
                    отчёты за дни доставки или самовывоза.
                  </p>
                ) : null}
              </div>

              {report ? (
                <div className="no-print">
                  <ReportSummaryCards data={report.data} />
                </div>
              ) : null}
            </>
          )}
        </div>

        <Card title="История отчётов" className="no-print w-full shrink-0 lg:w-80">
          <ReportHistoryList
            items={historyQuery.data}
            activeDate={effectiveDate}
            onSelect={(date) => setSelectedDate(date)}
            loading={historyQuery.isLoading}
            error={historyQuery.error}
            onRetry={() => historyQuery.refetch()}
          />
        </Card>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title="Пересобрать отчёт?"
        description={
          effectiveDate
            ? `Отчёт за ${formatDateLong(effectiveDate)} будет построен заново на основе текущих данных.`
            : undefined
        }
        confirmLabel="Пересобрать"
        loading={regenerating}
        onConfirm={handleRegenerate}
        onCancel={() => setConfirmOpen(false)}
      />
    </>
  );
}
