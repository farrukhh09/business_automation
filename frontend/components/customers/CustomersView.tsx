"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { CustomerFormModal } from "@/components/customers/CustomerFormModal";
import { CustomerTypeBadge } from "@/components/shared/StatusBadges";
import { FilterBar, FilterBarItem } from "@/components/shared/FilterBar";
import { MoneyText } from "@/components/shared/MoneyText";
import { PhoneLink } from "@/components/shared/CustomerLink";
import { Button } from "@/components/ui/Button";
import { EnumBadge } from "@/components/ui/Badge";
import { IconPlus, IconSearch } from "@/components/ui/icons";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Pagination } from "@/components/ui/Pagination";
import { Select } from "@/components/ui/Select";
import { DataTable, type DataTableColumn } from "@/components/ui/Table";
import { useDebouncedValue } from "@/lib/hooks";
import { LANGUAGE_LABELS, LANGUAGE_TONES, CUSTOMER_TYPE_FILTER_LABELS, optionsOf } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { customerDisplayName, formatDateTime, formatNumber } from "@/lib/format";
import { defineUrlState, urlParam, useUrlState } from "@/lib/url-state";
import { customersApi } from "@/services/api";
import { CUSTOMER_SORTS, CUSTOMER_TYPE_FILTERS, type CustomerListItem, type CustomerSort, type CustomerTypeFilter } from "@/types/api";

const CUSTOMERS_FILTERS = defineUrlState({
  search: urlParam.string(),
  customer_type: urlParam.enum(CUSTOMER_TYPE_FILTERS),
  sort: urlParam.enum(CUSTOMER_SORTS, "-last_order_at"),
  page: urlParam.page(),
});

const CUSTOMER_TYPE_OPTIONS = optionsOf(CUSTOMER_TYPE_FILTER_LABELS);

const SORT_OPTIONS = [
  { value: "-last_order_at", label: "Сначала последний заказ" },
  { value: "last_order_at", label: "Сначала давний заказ" },
  { value: "-created_at", label: "Сначала новые в базе" },
  { value: "created_at", label: "Сначала старые в базе" },
  { value: "-total_spent", label: "По убыванию суммы" },
  { value: "total_spent", label: "По возрастанию суммы" },
] as const satisfies readonly { value: CustomerSort; label: string }[];

const PAGE_SIZE = 20;

export function CustomersView() {
  const router = useRouter();
  const { state, setState, setPage, reset, activeCount } = useUrlState(CUSTOMERS_FILTERS.defaults, CUSTOMERS_FILTERS);
  const [searchInput, setSearchInput] = useState(state.search);
  const debouncedSearch = useDebouncedValue(searchInput, 350);
  const [createOpen, setCreateOpen] = useState(false);

  useEffect(() => {
    setState({ search: debouncedSearch });
  }, [debouncedSearch, setState]);

  // Keep the box in sync when the URL search term changes from elsewhere (reset button, back/forward).
  const [syncedSearch, setSyncedSearch] = useState(state.search);
  if (state.search !== syncedSearch) {
    setSyncedSearch(state.search);
    setSearchInput(state.search);
  }

  const params = {
    search: state.search || undefined,
    customer_type: (state.customer_type || undefined) as CustomerTypeFilter | undefined,
    sort: state.sort,
    page: state.page,
    page_size: PAGE_SIZE,
  };

  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: queryKeys.customers.list(params),
    queryFn: () => customersApi.list(params),
    placeholderData: keepPreviousData,
  });

  const columns: DataTableColumn<CustomerListItem>[] = [
    {
      key: "name",
      header: "Имя",
      cell: (customer) => (
        <div className="flex flex-col">
          <span className="font-medium text-slate-900">{customerDisplayName(customer)}</span>
          {customer.username ? <span className="text-xs text-slate-500">@{customer.username}</span> : null}
        </div>
      ),
    },
    {
      key: "phone",
      header: "Телефон",
      cell: (customer) => <PhoneLink phone={customer.phone} />,
    },
    {
      key: "orders_count",
      header: "Заказов",
      align: "right",
      cell: (customer) => formatNumber(customer.orders_count, 0),
    },
    {
      key: "total_spent",
      header: "Сумма заказов",
      align: "right",
      cell: (customer) => <MoneyText value={customer.total_spent} strong />,
    },
    {
      key: "last_order_at",
      header: "Последний заказ",
      cell: (customer) => formatDateTime(customer.last_order_at),
    },
    {
      key: "customer_type",
      header: "Статус",
      cell: (customer) => <CustomerTypeBadge type={customer.customer_type} />,
    },
    {
      key: "language",
      header: "Язык",
      cell: (customer) => <EnumBadge value={customer.language} labels={LANGUAGE_LABELS} tones={LANGUAGE_TONES} />,
    },
  ];

  return (
    <>
      <PageHeader
        title="Клиенты"
        description="Новые и постоянные клиенты, история заказов"
        actions={
          <Button leftIcon={<IconPlus className="size-4" />} onClick={() => setCreateOpen(true)}>
            Добавить клиента
          </Button>
        }
      />

      <div className="flex flex-col gap-4">
        <FilterBar
          search={
            <Input
              label="Поиск"
              placeholder="Имя, username или телефон"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              prefix={<IconSearch className="size-4" />}
              containerClassName="sm:min-w-0"
            />
          }
          onReset={reset}
          activeCount={activeCount}
          columns={2}
        >
          <FilterBarItem>
            <Select
              label="Тип клиента"
              placeholder="Все"
              options={CUSTOMER_TYPE_OPTIONS}
              value={state.customer_type}
              onChange={(event) => setState({ customer_type: event.target.value as CustomerTypeFilter | "" })}
            />
          </FilterBarItem>
          <FilterBarItem>
            <Select
              label="Сортировка"
              options={SORT_OPTIONS}
              value={state.sort}
              onChange={(event) => setState({ sort: event.target.value as CustomerSort })}
            />
          </FilterBarItem>
        </FilterBar>

        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
          <DataTable
            columns={columns}
            rows={data?.items}
            rowKey={(customer) => customer.id}
            loading={isLoading || isFetching}
            error={error}
            onRetry={refetch}
            onRowClick={(customer) => router.push(`/customers/${customer.id}`)}
            emptyTitle="Клиенты не найдены"
            emptyDescription="Измените фильтры или добавьте нового клиента."
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

      {createOpen ? <CustomerFormModal onClose={() => setCreateOpen(false)} /> : null}
    </>
  );
}
