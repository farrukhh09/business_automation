"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ConversationModeBadge } from "@/components/shared/StatusBadges";
import { CustomerLink } from "@/components/shared/CustomerLink";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { IconRefresh, IconSearch } from "@/components/ui/icons";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Pagination } from "@/components/ui/Pagination";
import { Tabs, type TabItem } from "@/components/ui/Tabs";
import { DataTable, type DataTableColumn } from "@/components/ui/Table";
import { useDebouncedValue } from "@/lib/hooks";
import { formatDateTime, formatOrderNumber } from "@/lib/format";
import { queryKeys } from "@/lib/query";
import { defineUrlState, urlParam, useUrlState } from "@/lib/url-state";
import { conversationsApi } from "@/services/api";
import type { ConversationListItem, ConversationListParams } from "@/types/api";

const FILTER_VALUES = ["all", "needs_attention", "handoff", "ai"] as const;
type FilterValue = (typeof FILTER_VALUES)[number];

const CONVERSATIONS_FILTERS = defineUrlState({
  filter: urlParam.enum(FILTER_VALUES, "all"),
  search: urlParam.string(),
  page: urlParam.page(),
});

const TABS: readonly TabItem<FilterValue>[] = [
  { value: "all", label: "Все" },
  { value: "needs_attention", label: "Требуют внимания" },
  { value: "handoff", label: "У оператора" },
  { value: "ai", label: "Бот" },
];

const PAGE_SIZE = 20;

/** `"5 мин назад"` / `"2 ч назад"` / `"3 дн назад"`; falls back to an absolute date+time. */
function relativeTimeFromNow(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  const ms = Date.now() - date.getTime();
  if (Number.isNaN(ms)) return "—";
  if (ms < 45_000) return "только что";
  const minutes = Math.round(ms / 60_000);
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ч назад`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days} дн назад`;
  return formatDateTime(value);
}

function buildParams(filter: FilterValue, search: string, page: number): ConversationListParams {
  const params: ConversationListParams = { search: search || undefined, page, page_size: PAGE_SIZE };
  if (filter === "needs_attention") params.needs_attention = true;
  else if (filter === "handoff") params.mode = "HUMAN_HANDOFF";
  else if (filter === "ai") params.mode = "AI";
  return params;
}

export function ConversationsView() {
  const router = useRouter();
  const { state, setState, setPage, reset, activeCount } = useUrlState(
    CONVERSATIONS_FILTERS.defaults,
    CONVERSATIONS_FILTERS,
  );
  const [searchInput, setSearchInput] = useState(state.search);
  const debouncedSearch = useDebouncedValue(searchInput, 350);

  useEffect(() => {
    setState({ search: debouncedSearch });
  }, [debouncedSearch, setState]);

  // Keep the box in sync when the URL search term changes from elsewhere (reset button, back/forward).
  const [syncedSearch, setSyncedSearch] = useState(state.search);
  if (state.search !== syncedSearch) {
    setSyncedSearch(state.search);
    setSearchInput(state.search);
  }

  const params = buildParams(state.filter, state.search, state.page);

  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: queryKeys.conversations.list(params),
    queryFn: () => conversationsApi.list(params),
    placeholderData: keepPreviousData,
    refetchInterval: () => (typeof document !== "undefined" && document.visibilityState === "hidden" ? false : 15_000),
  });

  const columns: DataTableColumn<ConversationListItem>[] = [
    {
      key: "customer",
      header: "Клиент",
      cell: (conversation) => <CustomerLink customer={conversation.customer} showUsername />,
    },
    {
      key: "mode",
      header: "Режим",
      cell: (conversation) => <ConversationModeBadge mode={conversation.mode} />,
    },
    {
      key: "attention",
      header: "Внимание",
      cell: (conversation) =>
        conversation.needs_attention ? (
          <Badge tone="red" dot>
            Требует внимания
          </Badge>
        ) : (
          <span className="text-slate-400">—</span>
        ),
    },
    {
      key: "preview",
      header: "Последнее сообщение",
      cell: (conversation) => (
        <div className="flex max-w-80 flex-col">
          <span className="line-clamp-2 text-slate-700" title={conversation.last_message_preview ?? undefined}>
            {conversation.last_message_preview || <span className="text-slate-400">Сообщений нет</span>}
          </span>
          <span className="text-xs text-slate-400">{relativeTimeFromNow(conversation.last_message_at)}</span>
        </div>
      ),
    },
    {
      key: "order",
      header: "Заказ",
      cell: (conversation) =>
        conversation.active_order_id ? (
          <Link
            href={`/orders/${conversation.active_order_id}`}
            onClick={(event) => event.stopPropagation()}
            className="font-medium text-brand-700 hover:underline"
          >
            {formatOrderNumber(conversation.active_order_id)}
          </Link>
        ) : (
          <span className="text-slate-400">—</span>
        ),
    },
  ];

  return (
    <>
      <PageHeader title="Диалоги" description="Переписка с клиентами в Instagram" />

      <div className="flex flex-col gap-4">
        <Tabs
          items={TABS}
          value={state.filter}
          onValueChange={(value) => setState({ filter: value })}
          ariaLabel="Фильтр диалогов"
        />

        <div className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-3 sm:flex-row sm:items-end sm:p-4">
          <Input
            label="Поиск"
            placeholder="Имя, username или телефон клиента"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            prefix={<IconSearch className="size-4" />}
            containerClassName="min-w-0 flex-1"
          />
          <Button
            variant="ghost"
            size="sm"
            onClick={reset}
            disabled={activeCount === 0}
            leftIcon={<IconRefresh className="size-4" />}
          >
            Сбросить
          </Button>
        </div>

        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
          <DataTable
            columns={columns}
            rows={data?.items}
            rowKey={(conversation) => conversation.id}
            loading={isLoading || isFetching}
            error={error}
            onRetry={refetch}
            onRowClick={(conversation) => router.push(`/conversations/${conversation.id}`)}
            emptyTitle="Диалогов не найдено"
            emptyDescription="Измените фильтры или подождите новых сообщений от клиентов."
          />
          <Pagination
            page={state.page}
            pageSize={PAGE_SIZE}
            total={data?.total ?? 0}
            onPageChange={setPage}
            disabled={isFetching}
            className="border-t border-slate-100 px-4"
          />
        </div>
      </div>
    </>
  );
}
