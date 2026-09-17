"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ProductFormModal } from "@/components/products/ProductFormModal";
import { MoneyText } from "@/components/shared";
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
import { productsApi } from "@/services/api";
import type { ProductOut } from "@/types/api";

export default function ProductsPage() {
  const { isAdmin } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();

  const [includeInactive, setIncludeInactive] = useState(false);
  /** `undefined` = modal closed, `null` = create mode, `ProductOut` = edit mode. */
  const [modalProduct, setModalProduct] = useState<ProductOut | null | undefined>(undefined);
  const [deleteTarget, setDeleteTarget] = useState<ProductOut | null>(null);

  const params = useMemo(() => ({ include_inactive: includeInactive }), [includeInactive]);
  const {
    data: products,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: queryKeys.products.list(params),
    queryFn: () => productsApi.list(params),
  });

  const toggleActive = useMutation({
    mutationFn: (product: ProductOut) => productsApi.update(product.id, { is_active: !product.is_active }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.products.all });
      toast.success("Статус товара обновлён");
    },
    onError: (err) => toast.apiError(err, "Не удалось изменить статус товара"),
  });

  const remove = useMutation({
    mutationFn: (product: ProductOut) => productsApi.remove(product.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.products.all });
      toast.success("Товар удалён");
      setDeleteTarget(null);
    },
    onError: (err) => {
      toast.apiError(err, "Не удалось удалить товар");
      setDeleteTarget(null);
    },
  });

  const baseColumns: DataTableColumn<ProductOut>[] = [
    {
      key: "name",
      header: "Название",
      cell: (product) => (
        <div className="max-w-xs">
          <div className="font-medium text-slate-900">{product.name}</div>
          {product.description ? (
            <div className="mt-0.5 line-clamp-1 text-xs text-slate-500">{product.description}</div>
          ) : null}
        </div>
      ),
    },
    {
      key: "price",
      header: "Цена",
      cell: (product) => <MoneyText value={product.price} strong />,
    },
    {
      key: "unit",
      header: "Ед.",
      cell: (product) => product.unit,
    },
    {
      key: "aliases",
      header: "Алиасы",
      cell: (product) =>
        product.aliases.length ? (
          <div className="flex max-w-xs flex-wrap gap-1">
            {product.aliases.map((alias) => (
              <Badge key={alias} tone="gray">
                {alias}
              </Badge>
            ))}
          </div>
        ) : (
          <span className="text-slate-400">—</span>
        ),
    },
    {
      key: "sort_order",
      header: "Порядок",
      align: "center",
      cell: (product) => product.sort_order,
    },
  ];

  const activeColumn: DataTableColumn<ProductOut> = {
    key: "active",
    header: "Активен",
    align: "center",
    cell: (product) => (
      <Switch
        checked={product.is_active}
        onCheckedChange={() => toggleActive.mutate(product)}
        disabled={toggleActive.isPending}
        aria-label={product.is_active ? `Выключить «${product.name}»` : `Включить «${product.name}»`}
      />
    ),
  };

  const actionsColumn: DataTableColumn<ProductOut> = {
    key: "actions",
    header: "",
    align: "right",
    cell: (product) => (
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={() => setModalProduct(product)}>
          Изменить
        </Button>
        <Button variant="outline" size="sm" onClick={() => setDeleteTarget(product)}>
          Удалить
        </Button>
      </div>
    ),
  };

  const columns = isAdmin ? [...baseColumns, activeColumn, actionsColumn] : baseColumns;

  return (
    <>
      <PageHeader
        title="Товары"
        description="Каталог: цены, описание, активность. AI использует только активные товары."
        actions={
          isAdmin ? (
            <Button leftIcon={<IconPlus className="size-4" />} onClick={() => setModalProduct(null)}>
              Добавить товар
            </Button>
          ) : undefined
        }
      />

      <div className="mb-4 flex items-center rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-xs">
        <Switch checked={includeInactive} onCheckedChange={setIncludeInactive} label="Показать выключенные" />
      </div>

      <DataTable
        columns={columns}
        rows={products}
        rowKey={(product) => product.id}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        emptyTitle="Товаров пока нет"
        emptyDescription={isAdmin ? "Добавьте первый товар, чтобы бот мог предлагать его клиентам." : "Каталог пуст."}
      />

      {isAdmin ? (
        <>
          <ProductFormModal
            key={modalProduct === undefined ? "closed" : (modalProduct?.id ?? "create")}
            open={modalProduct !== undefined}
            onClose={() => setModalProduct(undefined)}
            product={modalProduct}
          />
          <ConfirmDialog
            open={deleteTarget !== null}
            title="Удалить товар?"
            description={`Товар «${deleteTarget?.name}» будет деактивирован (мягкое удаление) и скрыт из каталога. На уже оформленные заказы это не повлияет.`}
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
