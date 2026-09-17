"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { FaqFormModal } from "@/components/faq/FaqFormModal";
import { SegmentedControl } from "@/components/shared";
import {
  Badge,
  Button,
  ConfirmDialog,
  DataTable,
  IconPlus,
  PageHeader,
  Switch,
  useToast,
  type DataTableColumn,
} from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { queryKeys } from "@/lib/query";
import { faqApi } from "@/services/api";
import type { FaqOut } from "@/types/api";

export default function FaqPage() {
  const { isAdmin } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();

  const [includeInactive, setIncludeInactive] = useState(false);
  const [viewLanguage, setViewLanguage] = useState<"ru" | "tg">("ru");
  /** `undefined` = modal closed, `null` = create mode, `FaqOut` = edit mode. */
  const [modalFaq, setModalFaq] = useState<FaqOut | null | undefined>(undefined);
  const [deleteTarget, setDeleteTarget] = useState<FaqOut | null>(null);

  const params = useMemo(() => ({ include_inactive: includeInactive }), [includeInactive]);
  const {
    data: rawFaq,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: queryKeys.faq.list(params),
    queryFn: () => faqApi.list(params),
  });

  // The contract does not guarantee list order — sort by sort_order on the client to be safe.
  const faqList = useMemo(() => [...(rawFaq ?? [])].sort((a, b) => a.sort_order - b.sort_order), [rawFaq]);

  const toggleActive = useMutation({
    mutationFn: (faq: FaqOut) => faqApi.update(faq.id, { is_active: !faq.is_active }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.faq.all });
      toast.success("Статус вопроса обновлён");
    },
    onError: (err) => toast.apiError(err, "Не удалось изменить статус вопроса"),
  });

  const remove = useMutation({
    mutationFn: (faq: FaqOut) => faqApi.remove(faq.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.faq.all });
      toast.success("Вопрос удалён");
      setDeleteTarget(null);
    },
    onError: (err) => {
      toast.apiError(err, "Не удалось удалить вопрос");
      setDeleteTarget(null);
    },
  });

  const baseColumns: DataTableColumn<FaqOut>[] = [
    {
      key: "sort_order",
      header: "№",
      align: "center",
      cell: (faq) => <span className="text-slate-400">{faq.sort_order}</span>,
    },
    {
      key: "qa",
      header: "Вопрос и ответ",
      cell: (faq) => {
        const question = viewLanguage === "tg" ? (faq.question_tg ?? "") : faq.question;
        const answer = viewLanguage === "tg" ? (faq.answer_tg ?? "") : faq.answer;
        const missingTg = viewLanguage === "tg" && !faq.question_tg && !faq.answer_tg;
        return (
          <div className="max-w-xl">
            <div className="font-medium text-slate-900">
              {missingTg ? <span className="text-slate-400 italic">Перевод не заполнен</span> : question}
            </div>
            {!missingTg ? <div className="mt-0.5 text-sm text-slate-600">{answer}</div> : null}
          </div>
        );
      },
    },
    {
      key: "keywords",
      header: "Ключевые слова",
      cell: (faq) =>
        faq.keywords.length ? (
          <div className="flex max-w-xs flex-wrap gap-1">
            {faq.keywords.map((keyword) => (
              <Badge key={keyword} tone="gray">
                {keyword}
              </Badge>
            ))}
          </div>
        ) : (
          <span className="text-slate-400">—</span>
        ),
    },
  ];

  const activeColumn: DataTableColumn<FaqOut> = {
    key: "active",
    header: "Активен",
    align: "center",
    cell: (faq) => (
      <Switch
        checked={faq.is_active}
        onCheckedChange={() => toggleActive.mutate(faq)}
        disabled={toggleActive.isPending}
        aria-label={faq.is_active ? "Выключить вопрос" : "Включить вопрос"}
      />
    ),
  };

  const actionsColumn: DataTableColumn<FaqOut> = {
    key: "actions",
    header: "",
    align: "right",
    cell: (faq) => (
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={() => setModalFaq(faq)}>
          Изменить
        </Button>
        <Button variant="outline" size="sm" onClick={() => setDeleteTarget(faq)}>
          Удалить
        </Button>
      </div>
    ),
  };

  const columns = isAdmin ? [...baseColumns, activeColumn, actionsColumn] : baseColumns;

  return (
    <>
      <PageHeader
        title="FAQ"
        description="Бот отвечает клиентам только на вопросы из этого списка — добавляйте и редактируйте их здесь."
        actions={
          isAdmin ? (
            <Button leftIcon={<IconPlus className="size-4" />} onClick={() => setModalFaq(null)}>
              Добавить вопрос
            </Button>
          ) : undefined
        }
      />

      <div className="mb-4 flex flex-col gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-xs sm:flex-row sm:items-center sm:justify-between">
        <Switch checked={includeInactive} onCheckedChange={setIncludeInactive} label="Показать выключенные" />
        <SegmentedControl
          ariaLabel="Язык отображения"
          size="sm"
          value={viewLanguage}
          onChange={setViewLanguage}
          options={[
            { value: "ru", label: "Русский" },
            { value: "tg", label: "Таджикский" },
          ]}
        />
      </div>

      <DataTable
        columns={columns}
        rows={faqList}
        rowKey={(faq) => faq.id}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        emptyTitle="Вопросов пока нет"
        emptyDescription={isAdmin ? "Добавьте первый вопрос, чтобы бот мог на него отвечать." : "Список пуст."}
      />

      {isAdmin ? (
        <>
          <FaqFormModal
            key={modalFaq === undefined ? "closed" : (modalFaq?.id ?? "create")}
            open={modalFaq !== undefined}
            onClose={() => setModalFaq(undefined)}
            faq={modalFaq}
          />
          <ConfirmDialog
            open={deleteTarget !== null}
            title="Удалить вопрос?"
            description="Вопрос будет удалён безвозвратно, бот больше не будет использовать его для ответов."
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
