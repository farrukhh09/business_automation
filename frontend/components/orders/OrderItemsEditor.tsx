"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { MoneyText } from "@/components/shared/MoneyText";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Spinner";
import { IconClose } from "@/components/ui/icons";
import { formatMoney } from "@/lib/format";
import { queryKeys } from "@/lib/query";
import { productsApi } from "@/services/api";

export interface OrderItemDraft {
  product_id: number;
  quantity: number;
  comment?: string | null;
  /** Snapshot label shown when the product is no longer in the active catalog (existing order items). */
  productLabel?: string;
  /** Snapshot unit price used for the preliminary total only when the product isn't active any more. */
  unitPriceHint?: number;
}

export interface OrderItemsEditorProps {
  items: OrderItemDraft[];
  onChange: (items: OrderItemDraft[]) => void;
  /** Shown under the list, e.g. "Добавьте хотя бы одну позицию". */
  error?: string;
  disabled?: boolean;
}

/**
 * Order item editor: pick a product from the active catalog, quantity stepper, optional per-item
 * comment, remove button, and a live client-side preliminary total (server computes the real one).
 */
export function OrderItemsEditor({ items, onChange, error, disabled = false }: OrderItemsEditorProps) {
  const productsQuery = useQuery({
    queryKey: queryKeys.products.list({}),
    queryFn: () => productsApi.list(),
  });
  const products = useMemo(() => productsQuery.data ?? [], [productsQuery.data]);
  const productById = useMemo(() => new Map(products.map((product) => [product.id, product])), [products]);

  const priceOf = (item: OrderItemDraft): number => productById.get(item.product_id)?.price ?? item.unitPriceHint ?? 0;
  const nameOf = (item: OrderItemDraft): string =>
    productById.get(item.product_id)?.name ?? item.productLabel ?? `Товар #${item.product_id}`;

  const total = items.reduce((sum, item) => sum + priceOf(item) * item.quantity, 0);
  const availableToAdd = products.filter((product) => !items.some((item) => item.product_id === product.id));

  const updateItem = (index: number, patch: Partial<OrderItemDraft>) => {
    onChange(items.map((item, i) => (i === index ? { ...item, ...patch } : item)));
  };

  const removeItem = (index: number) => onChange(items.filter((_, i) => i !== index));

  const addProduct = (productId: number) => {
    const existingIndex = items.findIndex((item) => item.product_id === productId);
    if (existingIndex >= 0) {
      updateItem(existingIndex, { quantity: items[existingIndex].quantity + 1 });
      return;
    }
    onChange([...items, { product_id: productId, quantity: 1, comment: "" }]);
  };

  return (
    <div className="flex flex-col gap-3">
      {productsQuery.isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : items.length === 0 ? (
        <p className="text-sm text-slate-500">Товары не добавлены.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {items.map((item, index) => (
            <li key={`${item.product_id}-${index}`} className="rounded-lg border border-slate-200 p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium text-slate-900">{nameOf(item)}</p>
                  <p className="text-sm text-slate-500">{formatMoney(priceOf(item))} за шт.</p>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={() => removeItem(index)}
                  disabled={disabled}
                  aria-label="Удалить позицию"
                  title="Удалить позицию"
                >
                  <IconClose className="size-4" />
                </Button>
              </div>
              <div className="mt-3 flex flex-wrap items-end gap-3">
                <div className="flex items-center gap-1.5">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => updateItem(index, { quantity: Math.max(1, item.quantity - 1) })}
                    disabled={disabled || item.quantity <= 1}
                    aria-label="Уменьшить количество"
                  >
                    −
                  </Button>
                  <Input
                    aria-label="Количество"
                    type="number"
                    min={1}
                    value={item.quantity}
                    onChange={(event) => {
                      const next = Math.max(1, Math.trunc(Number(event.target.value) || 1));
                      updateItem(index, { quantity: next });
                    }}
                    disabled={disabled}
                    className="w-16 text-center"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => updateItem(index, { quantity: item.quantity + 1 })}
                    disabled={disabled}
                    aria-label="Увеличить количество"
                  >
                    +
                  </Button>
                </div>
                <Input
                  label="Комментарий к позиции"
                  placeholder="Необязательно"
                  value={item.comment ?? ""}
                  onChange={(event) => updateItem(index, { comment: event.target.value })}
                  disabled={disabled}
                  containerClassName="min-w-48 flex-1"
                />
                <MoneyText value={priceOf(item) * item.quantity} strong className="ml-auto" />
              </div>
            </li>
          ))}
        </ul>
      )}

      {error ? <p className="text-sm text-red-600">{error}</p> : null}

      {!disabled ? (
        <Select
          aria-label="Добавить товар"
          placeholder={availableToAdd.length ? "Добавить товар…" : "Все товары уже в заказе"}
          value=""
          disabled={availableToAdd.length === 0 || productsQuery.isLoading}
          onChange={(event) => {
            const id = Number(event.target.value);
            if (id) addProduct(id);
          }}
          options={availableToAdd.map((product) => ({
            value: product.id,
            label: `${product.name} — ${formatMoney(product.price)}`,
          }))}
        />
      ) : null}

      <div className="flex items-center justify-between border-t border-slate-100 pt-3">
        <span className="text-sm text-slate-500">Предварительно — сумму считает сервер</span>
        <MoneyText value={total} strong className="text-base" />
      </div>
    </div>
  );
}
