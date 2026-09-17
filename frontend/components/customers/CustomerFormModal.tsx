"use client";

import { useQueryClient, useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { useToast } from "@/components/ui/Toast";
import { optionsOf } from "@/lib/labels";
import { LANGUAGE_LABELS } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { isApiError } from "@/services/http";
import { customersApi } from "@/services/api";
import type { CustomerDetail, Language } from "@/types/api";

const LANGUAGE_OPTIONS = optionsOf(LANGUAGE_LABELS);

export interface CustomerFormModalProps {
  onClose: () => void;
  /** Called after the customer was created successfully. */
  onCreated?: (customer: CustomerDetail) => void;
}

interface FormState {
  name: string;
  phone: string;
  language: Language;
  notes: string;
}

const EMPTY_FORM: FormState = { name: "", phone: "", language: "ru", notes: "" };

/**
 * "Добавить клиента" modal (name, phone, language, notes) → POST /customers.
 * Mounted only while open (see CustomersView), so its form state always starts fresh.
 */
export function CustomerFormModal({ onClose, onCreated }: CustomerFormModalProps) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [nameError, setNameError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      customersApi.create({
        name: form.name.trim(),
        phone: form.phone.trim() || undefined,
        language: form.language,
        notes: form.notes.trim() || undefined,
      }),
    onSuccess: (customer) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.customers.all });
      toast.success("Клиент добавлен");
      onCreated?.(customer);
      onClose();
    },
    onError: (error) => {
      toast.apiError(error, "Не удалось добавить клиента");
    },
  });

  const fieldErrors = isApiError(mutation.error) ? mutation.error.fieldErrors : {};

  const handleSubmit = () => {
    const trimmedName = form.name.trim();
    if (!trimmedName) {
      setNameError("Укажите имя клиента");
      return;
    }
    setNameError(null);
    mutation.mutate();
  };

  return (
    <Modal
      open
      onClose={() => {
        if (!mutation.isPending) onClose();
      }}
      title="Добавить клиента"
      dismissible={!mutation.isPending}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>
            Отмена
          </Button>
          <Button onClick={handleSubmit} loading={mutation.isPending}>
            Добавить
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Input
          label="Имя"
          required
          value={form.name}
          onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
          error={nameError ?? fieldErrors.name}
          autoFocus
        />
        <Input
          label="Телефон"
          type="tel"
          value={form.phone}
          onChange={(event) => setForm((prev) => ({ ...prev, phone: event.target.value }))}
          error={fieldErrors.phone}
          placeholder="+992 ..."
        />
        <Select
          label="Язык"
          options={LANGUAGE_OPTIONS}
          value={form.language}
          onChange={(event) => setForm((prev) => ({ ...prev, language: event.target.value as Language }))}
        />
        <Textarea
          label="Заметки"
          value={form.notes}
          onChange={(event) => setForm((prev) => ({ ...prev, notes: event.target.value }))}
          error={fieldErrors.notes}
          rows={3}
        />
      </div>
    </Modal>
  );
}
