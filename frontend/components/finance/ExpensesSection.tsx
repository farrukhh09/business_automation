"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ExpenseFormModal } from "@/components/finance/ExpenseFormModal";
import { MoneyText, SectionCard } from "@/components/shared";
import {
  Button,
  ConfirmDialog,
  DataTable,
  EnumBadge,
  IconPlus,
  Pagination,
  Select,
  useToast,
  type DataTableColumn,
} from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";
import { EXPENSE_CATEGORY_LABELS, EXPENSE_CATEGORY_TONES, optionsOf } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import type { DateRange } from "@/lib/url-state";
import { expensesApi } from "@/services/api";
import type { ExpenseCategory, ExpenseOut } from "@/types/api";

const PAGE_SIZE = 20;
const CATEGORY_FILTER_OPTIONS = optionsOf(EXPENSE_CATEGORY_LABELS);

export interface ExpensesSectionProps {
  /** The page period; `null` while a custom range is incomplete. Remount on change (`key`) to reset paging. */
  range: DateRange | null;
}

/** Expenses of the selected period: list, category filter, add/edit/delete for ADMIN. */
export function ExpensesSection({ range }: ExpensesSectionProps) {
  const { isAdmin } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [category, setCategory] = useState<ExpenseCategory | "">("");
  const [page, setPage] = useState(1);
  /** `undefined` = modal closed, `null` = create mode, `ExpenseOut` = edit mode. */
  const [modalExpense, setModalExpense] = useState<ExpenseOut | null | undefined>(undefined);
  const [deleteTarget, setDeleteTarget] = useState<ExpenseOut | null>(null);

  const params = {
    date_from: range?.date_from,
    date_to: range?.date_to,
    category: category || undefined,
    page,
    page_size: PAGE_SIZE,
  };
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: queryKeys.expenses.list(params),
    queryFn: () => expensesApi.list(params),
    enabled: range !== null,
    placeholderData: keepPreviousData,
  });

  const remove = useMutation({
    mutationFn: (expense: ExpenseOut) => expensesApi.remove(expense.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.expenses.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.statistics.all });
      toast.success("Расход удалён");
      setDeleteTarget(null);
    },
    onError: (err) => {
      toast.apiError(err, "Не удалось удалить расход");
      setDeleteTarget(null);
    },
  });

  const columns: DataTableColumn<ExpenseOut>[] = [
    {
      key: "date",
      header: "Дата",
      cell: (expense) => <span className="whitespace-nowrap">{formatDate(expense.expense_date)}</span>,
    },
    {
      key: "category",
      header: "Категория",
      cell: (expense) => (
        <EnumBadge value={expense.category} labels={EXPENSE_CATEGORY_LABELS} tones={EXPENSE_CATEGORY_TONES} />
      ),
    },
    {
      key: "comment",
      header: "Комментарий",
      cell: (expense) =>
        expense.comment ? (
          <span className="block max-w-md whitespace-pre-line text-slate-700">{expense.comment}</span>
        ) : (
          <span className="text-slate-400">—</span>
        ),
    },
    {
      key: "amount",
      header: "Сумма",
      align: "right",
      cell: (expense) => <MoneyText value={expense.amount} strong />,
    },
  ];

  if (isAdmin) {
    columns.push({
      key: "actions",
      header: "",
      align: "right",
      cell: (expense) => (
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setModalExpense(expense)}>
            Изменить
          </Button>
          <Button variant="outline" size="sm" onClick={() => setDeleteTarget(expense)}>
            Удалить
          </Button>
        </div>
      ),
    });
  }

  const addButton = isAdmin ? (
    <Button leftIcon={<IconPlus className="size-4" />} onClick={() => setModalExpense(null)}>
      Добавить расход
    </Button>
  ) : null;

  return (
    <>
      <SectionCard
        title="Расходы за период"
        description="Закупка продуктов, упаковка, доставка, реклама и другие траты — прибыль считается из них автоматически."
        actions={
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <Select
              aria-label="Категория расхода"
              placeholder="Все категории"
              options={CATEGORY_FILTER_OPTIONS}
              value={category}
              onChange={(event) => {
                setCategory(event.target.value as ExpenseCategory | "");
                setPage(1);
              }}
              containerClassName="sm:w-56"
            />
            {addButton}
          </div>
        }
        padded={false}
      >
        <DataTable
          columns={columns}
          rows={data?.items}
          rowKey={(expense) => expense.id}
          loading={isLoading || (isFetching && !data)}
          error={error}
          onRetry={refetch}
          emptyTitle={category ? "Расходов этой категории за период нет" : "Расходов за период нет"}
          emptyDescription={isAdmin ? "Добавьте первый расход — выручка, расходы и прибыль появятся в карточках выше." : undefined}
        />
        {data && data.total > PAGE_SIZE ? (
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={data.total}
            onPageChange={setPage}
            disabled={isFetching}
            className="border-t border-slate-100 px-4"
          />
        ) : null}
      </SectionCard>

      {isAdmin ? (
        <>
          <ExpenseFormModal
            key={modalExpense === undefined ? "closed" : (modalExpense?.id ?? "create")}
            open={modalExpense !== undefined}
            onClose={() => setModalExpense(undefined)}
            expense={modalExpense}
          />
          <ConfirmDialog
            open={deleteTarget !== null}
            title="Удалить расход?"
            description={
              deleteTarget
                ? `${EXPENSE_CATEGORY_LABELS[deleteTarget.category]}, ${formatDate(deleteTarget.expense_date)} — запись будет удалена безвозвратно, прибыль пересчитается.`
                : undefined
            }
            tone="danger"
            confirmLabel="Удалить"
            loading={remove.isPending}
            onConfirm={() => deleteTarget && remove.mutate(deleteTarget)}
            onCancel={() => setDeleteTarget(null)}
          />
        </>
      ) : null}
    </>
  );
}
