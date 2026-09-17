"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { useDebouncedValue } from "@/lib/hooks";
import { customerDisplayName } from "@/lib/format";
import { LANGUAGE_LABELS, optionsOf } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { customersApi } from "@/services/api";
import type { CustomerCreate, CustomerListItem, Language } from "@/types/api";

export interface CustomerPickerProps {
  selected: CustomerListItem | null;
  onSelect: (customer: CustomerListItem) => void;
  onClear: () => void;
  error?: string;
}

/**
 * Customer picker for the new-order form: debounced search over existing customers
 * (name / phone / username) plus an inline "Создать клиента" mini-form when there's no match.
 */
export function CustomerPicker({ selected, onSelect, onClear, error }: CustomerPickerProps) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const debounced = useDebouncedValue(search, 350);
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createPhone, setCreatePhone] = useState("");
  const [createUsername, setCreateUsername] = useState("");
  const [createLanguage, setCreateLanguage] = useState<Language>("ru");

  const trimmed = debounced.trim();
  const searchQuery = useQuery({
    queryKey: queryKeys.customers.list({ search: trimmed, page_size: 8 }),
    queryFn: () => customersApi.list({ search: trimmed, page_size: 8 }),
    enabled: !selected && trimmed.length > 0,
  });

  const createMutation = useMutation({
    mutationFn: (body: CustomerCreate) => customersApi.create(body),
    onSuccess: (customer) => {
      toast.success("Клиент создан");
      queryClient.invalidateQueries({ queryKey: queryKeys.customers.all });
      onSelect(customer);
      setShowCreate(false);
      setCreateName("");
      setCreatePhone("");
      setCreateUsername("");
    },
    onError: (err) => toast.apiError(err),
  });

  if (selected) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
        <div className="min-w-0">
          <p className="truncate font-medium text-slate-900">{customerDisplayName(selected)}</p>
          <p className="truncate text-sm text-slate-500">
            {[selected.username ? `@${selected.username}` : null, selected.phone].filter(Boolean).join(" · ") || "—"}
          </p>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={onClear}>
          Изменить
        </Button>
      </div>
    );
  }

  const results = searchQuery.data?.items ?? [];

  return (
    <div className="flex flex-col gap-2">
      <Input
        label="Клиент"
        placeholder="Имя, телефон или @username"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        error={error}
      />

      {trimmed.length > 0 ? (
        <div className="rounded-lg border border-slate-200">
          {searchQuery.isLoading ? (
            <div className="flex flex-col gap-2 p-3">
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
            </div>
          ) : results.length > 0 ? (
            <ul className="divide-y divide-slate-100">
              {results.map((customer) => (
                <li key={customer.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(customer)}
                    className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-none"
                  >
                    <span className="font-medium text-slate-900">{customerDisplayName(customer)}</span>
                    <span className="text-sm text-slate-500">
                      {[customer.username ? `@${customer.username}` : null, customer.phone].filter(Boolean).join(" · ") ||
                        "—"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="p-3 text-sm text-slate-500">Клиенты не найдены.</div>
          )}
        </div>
      ) : null}

      {!showCreate ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="self-start"
          onClick={() => {
            setShowCreate(true);
            setCreateName(search);
          }}
        >
          + Создать клиента
        </Button>
      ) : (
        <div className="flex flex-col gap-3 rounded-lg border border-slate-200 p-3">
          <p className="text-sm font-medium text-slate-700">Новый клиент</p>
          <Input label="Имя" value={createName} onChange={(event) => setCreateName(event.target.value)} required />
          <Input label="Телефон" type="tel" value={createPhone} onChange={(event) => setCreatePhone(event.target.value)} />
          <Input
            label="Instagram username"
            value={createUsername}
            onChange={(event) => setCreateUsername(event.target.value)}
          />
          <Select
            label="Язык"
            value={createLanguage}
            onChange={(event) => setCreateLanguage(event.target.value as Language)}
            options={optionsOf(LANGUAGE_LABELS)}
          />
          <div className="flex gap-2">
            <Button
              type="button"
              loading={createMutation.isPending}
              disabled={!createName.trim()}
              onClick={() =>
                createMutation.mutate({
                  name: createName.trim(),
                  phone: createPhone.trim() || undefined,
                  username: createUsername.trim() || undefined,
                  language: createLanguage,
                })
              }
            >
              Создать и выбрать
            </Button>
            <Button type="button" variant="outline" onClick={() => setShowCreate(false)}>
              Отмена
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
