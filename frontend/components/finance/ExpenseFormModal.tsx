"use client";

import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Button, DateInput, Input, Modal, Select, useToast } from "@/components/ui";
import { Textarea } from "@/components/ui/Textarea";
import { businessToday } from "@/lib/format";
import { EXPENSE_CATEGORY_LABELS, optionsOf } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { expensesApi } from "@/services/api";
import { isApiError } from "@/services/http";
import type { ExpenseCategory, ExpenseCreate, ExpenseOut } from "@/types/api";

export interface ExpenseFormModalProps {
  open: boolean;
  onClose: () => void;
  /** `null` → create mode, `ExpenseOut` → edit mode. */
  expense?: ExpenseOut | null;
}

/**
 * NOTE: the caller must remount this component (e.g. `key={expense?.id ?? "create"}`) whenever it is
 * opened for a different expense, so the form re-initialises without a reset-on-open effect.
 */

interface FormState {
  expense_date: string;
  category: ExpenseCategory;
  amount: string;
  comment: string;
}

const CATEGORY_OPTIONS = optionsOf(EXPENSE_CATEGORY_LABELS);

function toFormState(expense: ExpenseOut | null | undefined): FormState {
  if (!expense) return { expense_date: businessToday(), category: "INGREDIENTS", amount: "", comment: "" };
  return {
    expense_date: expense.expense_date,
    category: expense.category,
    amount: String(expense.amount),
    comment: expense.comment ?? "",
  };
}

/** Create/edit modal for one expense (ADMIN only). */
export function ExpenseFormModal({ open, onClose, expense }: ExpenseFormModalProps) {
  const isEdit = Boolean(expense);
  const queryClient = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState<FormState>(() => toFormState(expense));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const today = businessToday();

  const mutation = useMutation({
    mutationFn: () => {
      const body: ExpenseCreate = {
        expense_date: form.expense_date,
        category: form.category,
        amount: Number(form.amount.replace(",", ".")),
        comment: form.comment.trim() || null,
      };
      return isEdit && expense ? expensesApi.update(expense.id, body) : expensesApi.create(body);
    },
    onSuccess: () => {
      // Profit cards and the chart read /statistics — refresh them together with the list.
      queryClient.invalidateQueries({ queryKey: queryKeys.expenses.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.statistics.all });
      toast.success(isEdit ? "Расход обновлён" : "Расход добавлен");
      onClose();
    },
    onError: (error) => {
      if (isApiError(error) && Object.keys(error.fieldErrors).length > 0) setErrors(error.fieldErrors);
      toast.apiError(error, isEdit ? "Не удалось сохранить расход" : "Не удалось добавить расход");
    },
  });

  const validate = (): boolean => {
    const nextErrors: Record<string, string> = {};
    if (!form.expense_date) nextErrors.expense_date = "Укажите дату";
    else if (form.expense_date > today) nextErrors.expense_date = "Дата расхода не может быть в будущем";
    const amount = Number(form.amount.replace(",", "."));
    if (!form.amount.trim() || Number.isNaN(amount) || amount <= 0) nextErrors.amount = "Сумма должна быть больше нуля";
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!validate()) return;
    mutation.mutate();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isEdit ? "Редактировать расход" : "Новый расход"}
      size="md"
      dismissible={!mutation.isPending}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>
            Отмена
          </Button>
          <Button onClick={handleSubmit} loading={mutation.isPending}>
            {isEdit ? "Сохранить" : "Добавить"}
          </Button>
        </>
      }
    >
      <form className="flex flex-col gap-4" onSubmit={handleSubmit} noValidate>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <DateInput
            label="Дата"
            required
            value={form.expense_date}
            max={today}
            onValueChange={(expense_date) => setForm((f) => ({ ...f, expense_date }))}
            error={errors.expense_date}
          />
          <Input
            label="Сумма"
            required
            inputMode="decimal"
            suffix="сомони"
            value={form.amount}
            onChange={(event) => setForm((f) => ({ ...f, amount: event.target.value }))}
            error={errors.amount}
          />
        </div>
        <Select
          label="Категория"
          required
          options={CATEGORY_OPTIONS}
          value={form.category}
          onChange={(event) => setForm((f) => ({ ...f, category: event.target.value as ExpenseCategory }))}
          error={errors.category}
        />
        <Textarea
          label="Комментарий"
          hint="Что именно купили или оплатили — например, «мука 10 кг, сливочный сыр»"
          rows={3}
          value={form.comment}
          onChange={(event) => setForm((f) => ({ ...f, comment: event.target.value }))}
          error={errors.comment}
        />
      </form>
    </Modal>
  );
}
