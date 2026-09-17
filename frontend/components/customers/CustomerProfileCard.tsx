"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { useToast } from "@/components/ui/Toast";
import { CustomerTypeBadge } from "@/components/shared/StatusBadges";
import { KeyValueList } from "@/components/shared/KeyValueList";
import { SectionCard } from "@/components/shared/SectionCard";
import { EnumBadge } from "@/components/ui/Badge";
import { PhoneLink } from "@/components/shared/CustomerLink";
import { formatDateTime, formatMoney } from "@/lib/format";
import { LANGUAGE_LABELS, LANGUAGE_TONES, optionsOf } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { isApiError } from "@/services/http";
import { customersApi } from "@/services/api";
import type { CustomerDetail, Language } from "@/types/api";

const LANGUAGE_OPTIONS = optionsOf(LANGUAGE_LABELS);

export interface CustomerProfileCardProps {
  customer: CustomerDetail | undefined;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
}

interface FormState {
  name: string;
  phone: string;
  language: Language;
  notes: string;
}

function toFormState(customer: CustomerDetail): FormState {
  return {
    name: customer.name ?? "",
    phone: customer.phone ?? "",
    language: customer.language,
    notes: customer.notes ?? "",
  };
}

/** Profile card with inline edit (name, phone, language, notes) → PATCH /customers/{id}. */
export function CustomerProfileCard({ customer, loading, error, onRetry }: CustomerProfileCardProps) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<FormState | null>(null);
  const [nameError, setNameError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (body: FormState) =>
      customersApi.update(customer!.id, {
        name: body.name.trim(),
        phone: body.phone.trim() || null,
        language: body.language,
        notes: body.notes.trim() || null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.customers.all });
      toast.success("Данные клиента сохранены");
      setEditing(false);
      setForm(null);
    },
    onError: (error) => {
      toast.apiError(error, "Не удалось сохранить изменения");
    },
  });

  const fieldErrors = isApiError(mutation.error) ? mutation.error.fieldErrors : {};

  const startEditing = () => {
    if (!customer) return;
    setForm(toFormState(customer));
    setNameError(null);
    setEditing(true);
  };

  const cancelEditing = () => {
    setEditing(false);
    setForm(null);
    setNameError(null);
  };

  const save = () => {
    if (!form) return;
    if (!form.name.trim()) {
      setNameError("Укажите имя клиента");
      return;
    }
    setNameError(null);
    mutation.mutate(form);
  };

  return (
    <SectionCard
      title="Данные клиента"
      loading={loading}
      error={error}
      onRetry={onRetry}
      actions={
        !loading && !error && customer && !editing ? (
          <Button variant="outline" size="sm" onClick={startEditing}>
            Изменить
          </Button>
        ) : null
      }
    >
      {editing && form ? (
        <div className="flex flex-col gap-4">
          <Input
            label="Имя"
            required
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            error={nameError ?? fieldErrors.name}
          />
          <Input
            label="Телефон"
            type="tel"
            value={form.phone}
            onChange={(event) => setForm({ ...form, phone: event.target.value })}
            error={fieldErrors.phone}
          />
          <Select
            label="Язык"
            options={LANGUAGE_OPTIONS}
            value={form.language}
            onChange={(event) => setForm({ ...form, language: event.target.value as Language })}
          />
          <Textarea
            label="Заметки"
            value={form.notes}
            onChange={(event) => setForm({ ...form, notes: event.target.value })}
            error={fieldErrors.notes}
            rows={3}
          />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={cancelEditing} disabled={mutation.isPending}>
              Отмена
            </Button>
            <Button onClick={save} loading={mutation.isPending}>
              Сохранить
            </Button>
          </div>
        </div>
      ) : customer ? (
        <KeyValueList
          layout="rows"
          items={[
            { key: "name", label: "Имя", value: customer.name },
            { key: "username", label: "Instagram", value: customer.username ? `@${customer.username}` : null },
            { key: "phone", label: "Телефон", value: <PhoneLink phone={customer.phone} /> },
            {
              key: "language",
              label: "Язык",
              value: <EnumBadge value={customer.language} labels={LANGUAGE_LABELS} tones={LANGUAGE_TONES} />,
            },
            { key: "type", label: "Статус", value: <CustomerTypeBadge type={customer.customer_type} /> },
            { key: "orders_count", label: "Количество заказов", value: customer.orders_count },
            { key: "total_spent", label: "Сумма заказов", value: formatMoney(customer.total_spent) },
            { key: "last_order_at", label: "Последний заказ", value: formatDateTime(customer.last_order_at) },
            { key: "created_at", label: "В базе с", value: formatDateTime(customer.created_at) },
            { key: "notes", label: "Заметки", value: customer.notes },
          ]}
        />
      ) : null}
    </SectionCard>
  );
}
