"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { CopyButton } from "@/components/shared/CopyButton";
import { Modal } from "@/components/ui/Modal";
import { Select } from "@/components/ui/Select";
import { Input } from "@/components/ui/Input";
import { useToast } from "@/components/ui/Toast";
import { DELIVERY_STATUS_LABELS } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { deliveriesApi } from "@/services/api";
import type { DeliveryStatus, DeliveryUpdate, DispatchResultOut } from "@/types/api";

export interface DispatchModalProps {
  open: boolean;
  onClose: () => void;
  /** `null` while the dispatch card is being requested. */
  result: DispatchResultOut | null;
}

const DISPATCH_STATUS_OPTIONS: DeliveryStatus[] = ["AWAITING_DISPATCH", "DISPATCHED", "DELIVERED", "FAILED"];

interface FormState {
  external_id: string;
  courier_name: string;
  courier_phone: string;
  status: DeliveryStatus;
}

/** "Передать в Maxim" — POST /deliveries/{id}/dispatch then manual courier fields via PATCH. */
export function DispatchModal({ open, onClose, result }: DispatchModalProps) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const delivery = result?.delivery ?? null;
  const [form, setForm] = useState<FormState>({
    external_id: "",
    courier_name: "",
    courier_phone: "",
    status: "AWAITING_DISPATCH",
  });

  // Re-populate the form whenever a fresh dispatch result arrives — adjusted during render
  // instead of an effect (react.dev/learn/you-might-not-need-an-effect).
  const [lastResult, setLastResult] = useState<DispatchResultOut | null>(null);
  if (open && result && result !== lastResult) {
    setLastResult(result);
    setForm({
      external_id: result.delivery.external_id ?? "",
      courier_name: result.delivery.courier_name ?? "",
      courier_phone: result.delivery.courier_phone ?? "",
      status: result.delivery.status === "PENDING" ? "AWAITING_DISPATCH" : result.delivery.status,
    });
  }

  const mutation = useMutation({
    mutationFn: (body: DeliveryUpdate) => {
      if (!delivery) throw new Error("Карточка передачи ещё не готова");
      return deliveriesApi.update(delivery.id, body);
    },
    onSuccess: () => {
      toast.success("Данные курьера сохранены");
      queryClient.invalidateQueries({ queryKey: queryKeys.deliveries.lists });
      if (delivery) queryClient.invalidateQueries({ queryKey: queryKeys.deliveries.detail(delivery.id) });
      onClose();
    },
    onError: (error) => toast.apiError(error),
  });

  const save = () => {
    mutation.mutate({
      external_id: form.external_id.trim() || null,
      courier_name: form.courier_name.trim() || null,
      courier_phone: form.courier_phone.trim() || null,
      status: form.status,
    });
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Передать в Maxim"
      size="lg"
      dismissible={!mutation.isPending}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>
            Закрыть
          </Button>
          <Button onClick={save} loading={mutation.isPending} disabled={!delivery}>
            Сохранить данные курьера
          </Button>
        </>
      }
    >
      {!result ? (
        <p className="py-6 text-center text-sm text-slate-500">Формируем карточку…</p>
      ) : (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-slate-600">{result.instructions}</p>
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <pre className="max-h-56 overflow-auto font-mono text-xs whitespace-pre-wrap text-slate-800">
              {result.copy_text}
            </pre>
          </div>
          <CopyButton text={result.copy_text} label="Скопировать карточку" />

          <div className="border-t border-slate-100 pt-4">
            <p className="mb-3 text-sm font-medium text-slate-700">После оформления в приложении Maxim</p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Input
                label="Номер заказа в Maxim"
                value={form.external_id}
                onChange={(event) => setForm((current) => ({ ...current, external_id: event.target.value }))}
              />
              <Select
                label="Статус доставки"
                value={form.status}
                onChange={(event) =>
                  setForm((current) => ({ ...current, status: event.target.value as DeliveryStatus }))
                }
                options={DISPATCH_STATUS_OPTIONS.map((status) => ({ value: status, label: DELIVERY_STATUS_LABELS[status] }))}
              />
              <Input
                label="Курьер"
                value={form.courier_name}
                onChange={(event) => setForm((current) => ({ ...current, courier_name: event.target.value }))}
              />
              <Input
                label="Телефон курьера"
                type="tel"
                value={form.courier_phone}
                onChange={(event) => setForm((current) => ({ ...current, courier_phone: event.target.value }))}
              />
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
