"use client";

import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { TagsInput } from "@/components/products/TagsInput";
import { Button, Input, Modal, Switch, useToast } from "@/components/ui";
import { Textarea } from "@/components/ui/Textarea";
import { queryKeys } from "@/lib/query";
import { productsApi } from "@/services/api";
import { isApiError } from "@/services/http";
import type { ProductCreate, ProductOut } from "@/types/api";

export interface ProductFormModalProps {
  open: boolean;
  onClose: () => void;
  /** `null` → create mode, `ProductOut` → edit mode. */
  product?: ProductOut | null;
}

/**
 * NOTE: the caller must remount this component (e.g. `key={product?.id ?? "create"}`) whenever
 * it is opened for a different product, so the form re-initialises without a reset-on-open effect.
 */

interface FormState {
  name: string;
  description: string;
  price: string;
  unit: string;
  aliases: string[];
  is_active: boolean;
  sort_order: string;
}

const EMPTY_FORM: FormState = {
  name: "",
  description: "",
  price: "",
  unit: "шт.",
  aliases: [],
  is_active: true,
  sort_order: "0",
};

function toFormState(product: ProductOut | null | undefined): FormState {
  if (!product) return EMPTY_FORM;
  return {
    name: product.name,
    description: product.description ?? "",
    price: String(product.price),
    unit: product.unit,
    aliases: product.aliases,
    is_active: product.is_active,
    sort_order: String(product.sort_order),
  };
}

/** Create/edit modal for a catalogue product (ADMIN only). */
export function ProductFormModal({ open, onClose, product }: ProductFormModalProps) {
  const isEdit = Boolean(product);
  const queryClient = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState<FormState>(() => toFormState(product));
  const [errors, setErrors] = useState<Record<string, string>>({});

  const mutation = useMutation({
    mutationFn: () => {
      const body: ProductCreate = {
        name: form.name.trim(),
        description: form.description.trim() || null,
        price: Number(form.price.replace(",", ".")),
        currency: "TJS",
        unit: form.unit.trim() || "шт.",
        aliases: form.aliases,
        is_active: form.is_active,
        sort_order: Number(form.sort_order) || 0,
      };
      return isEdit && product ? productsApi.update(product.id, body) : productsApi.create(body);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.products.all });
      toast.success(isEdit ? "Товар обновлён" : "Товар создан");
      onClose();
    },
    onError: (error) => {
      if (isApiError(error) && Object.keys(error.fieldErrors).length > 0) setErrors(error.fieldErrors);
      toast.apiError(error, isEdit ? "Не удалось сохранить товар" : "Не удалось создать товар");
    },
  });

  const validate = (): boolean => {
    const nextErrors: Record<string, string> = {};
    if (!form.name.trim()) nextErrors.name = "Укажите название";
    const priceNum = Number(form.price.replace(",", "."));
    if (!form.price.trim() || Number.isNaN(priceNum) || priceNum <= 0) {
      nextErrors.price = "Цена должна быть больше нуля";
    }
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
      title={isEdit ? "Редактировать товар" : "Новый товар"}
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
        <Input
          label="Название"
          required
          value={form.name}
          onChange={(event) => setForm((f) => ({ ...f, name: event.target.value }))}
          error={errors.name}
        />
        <Textarea
          label="Описание"
          value={form.description}
          onChange={(event) => setForm((f) => ({ ...f, description: event.target.value }))}
          rows={3}
        />
        <div className="grid grid-cols-2 gap-4">
          <Input
            label="Цена"
            required
            type="number"
            min="0.01"
            step="0.01"
            inputMode="decimal"
            suffix="TJS"
            value={form.price}
            onChange={(event) => setForm((f) => ({ ...f, price: event.target.value }))}
            error={errors.price}
          />
          <Input label="Валюта" value="TJS" disabled hint="Валюта фиксирована для всех товаров" />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <Input
            label="Единица измерения"
            value={form.unit}
            onChange={(event) => setForm((f) => ({ ...f, unit: event.target.value }))}
          />
          <Input
            label="Порядок сортировки"
            type="number"
            step="1"
            value={form.sort_order}
            onChange={(event) => setForm((f) => ({ ...f, sort_order: event.target.value }))}
          />
        </div>
        <TagsInput
          label="Алиасы"
          hint="Другие названия товара, по которым бот распознаёт его в сообщениях клиента"
          value={form.aliases}
          onChange={(aliases) => setForm((f) => ({ ...f, aliases }))}
        />
        <Switch
          checked={form.is_active}
          onCheckedChange={(is_active) => setForm((f) => ({ ...f, is_active }))}
          label="Активен"
          description="Выключенный товар скрыт из каталога бота и панели заказов"
        />
      </form>
    </Modal>
  );
}
