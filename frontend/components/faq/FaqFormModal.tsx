"use client";

import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { TagsInput } from "@/components/products/TagsInput";
import { Button, Input, Modal, Switch, Tabs, useToast } from "@/components/ui";
import { Textarea } from "@/components/ui/Textarea";
import { queryKeys } from "@/lib/query";
import { faqApi } from "@/services/api";
import { isApiError } from "@/services/http";
import type { FaqCreate, FaqOut } from "@/types/api";

export interface FaqFormModalProps {
  open: boolean;
  onClose: () => void;
  /** `null` → create mode, `FaqOut` → edit mode. */
  faq?: FaqOut | null;
}

/**
 * NOTE: the caller must remount this component (e.g. `key={faq?.id ?? "create"}`) whenever it is
 * opened for a different FAQ entry, so the form re-initialises without a reset-on-open effect.
 */

interface FormState {
  question: string;
  answer: string;
  question_tg: string;
  answer_tg: string;
  keywords: string[];
  is_active: boolean;
  sort_order: string;
}

const EMPTY_FORM: FormState = {
  question: "",
  answer: "",
  question_tg: "",
  answer_tg: "",
  keywords: [],
  is_active: true,
  sort_order: "0",
};

function toFormState(faq: FaqOut | null | undefined): FormState {
  if (!faq) return EMPTY_FORM;
  return {
    question: faq.question,
    answer: faq.answer,
    question_tg: faq.question_tg ?? "",
    answer_tg: faq.answer_tg ?? "",
    keywords: faq.keywords,
    is_active: faq.is_active,
    sort_order: String(faq.sort_order),
  };
}

/** Create/edit modal for one FAQ entry (ADMIN only). */
export function FaqFormModal({ open, onClose, faq }: FaqFormModalProps) {
  const isEdit = Boolean(faq);
  const queryClient = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState<FormState>(() => toFormState(faq));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [tab, setTab] = useState<"ru" | "tg">("ru");

  const mutation = useMutation({
    mutationFn: () => {
      const body: FaqCreate = {
        question: form.question.trim(),
        answer: form.answer.trim(),
        question_tg: form.question_tg.trim() || null,
        answer_tg: form.answer_tg.trim() || null,
        keywords: form.keywords,
        is_active: form.is_active,
        sort_order: Number(form.sort_order) || 0,
      };
      return isEdit && faq ? faqApi.update(faq.id, body) : faqApi.create(body);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.faq.all });
      toast.success(isEdit ? "Вопрос обновлён" : "Вопрос добавлен");
      onClose();
    },
    onError: (error) => {
      if (isApiError(error) && Object.keys(error.fieldErrors).length > 0) setErrors(error.fieldErrors);
      toast.apiError(error, isEdit ? "Не удалось сохранить вопрос" : "Не удалось добавить вопрос");
    },
  });

  const validate = (): boolean => {
    const nextErrors: Record<string, string> = {};
    if (!form.question.trim()) nextErrors.question = "Укажите текст вопроса";
    if (!form.answer.trim()) nextErrors.answer = "Укажите текст ответа";
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) setTab("ru");
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
      title={isEdit ? "Редактировать вопрос" : "Новый вопрос"}
      size="lg"
      dismissible={!mutation.isPending}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>
            Отмена
          </Button>
          <Button onClick={handleSubmit} loading={mutation.isPending}>
            {isEdit ? "Сохранить" : "Создать"}
          </Button>
        </>
      }
    >
      <form className="flex flex-col gap-4" onSubmit={handleSubmit} noValidate>
        <Tabs
          value={tab}
          onValueChange={setTab}
          ariaLabel="Язык вопроса и ответа"
          items={[
            { value: "ru", label: "Русский" },
            { value: "tg", label: "Таджикский (необязательно)" },
          ]}
        />
        {tab === "ru" ? (
          <div className="flex flex-col gap-4">
            <Input
              label="Вопрос"
              required
              value={form.question}
              onChange={(event) => setForm((f) => ({ ...f, question: event.target.value }))}
              error={errors.question}
            />
            <Textarea
              label="Ответ"
              required
              rows={4}
              value={form.answer}
              onChange={(event) => setForm((f) => ({ ...f, answer: event.target.value }))}
              error={errors.answer}
            />
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <Input
              label="Вопрос (тадж.)"
              hint="Необязательно — если не заполнено, бот отвечает по-русски"
              value={form.question_tg}
              onChange={(event) => setForm((f) => ({ ...f, question_tg: event.target.value }))}
            />
            <Textarea
              label="Ответ (тадж.)"
              rows={4}
              value={form.answer_tg}
              onChange={(event) => setForm((f) => ({ ...f, answer_tg: event.target.value }))}
            />
          </div>
        )}
        <TagsInput
          label="Ключевые слова"
          hint="Помогают боту сопоставить вопрос клиента с этой записью"
          value={form.keywords}
          onChange={(keywords) => setForm((f) => ({ ...f, keywords }))}
        />
        <div className="grid grid-cols-2 gap-4">
          <Input
            label="Порядок сортировки"
            type="number"
            step="1"
            value={form.sort_order}
            onChange={(event) => setForm((f) => ({ ...f, sort_order: event.target.value }))}
          />
          <div className="flex items-end">
            <Switch
              checked={form.is_active}
              onCheckedChange={(is_active) => setForm((f) => ({ ...f, is_active }))}
              label="Активен"
            />
          </div>
        </div>
      </form>
    </Modal>
  );
}
