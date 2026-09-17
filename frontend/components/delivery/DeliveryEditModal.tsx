"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { queryKeys } from "@/lib/query";
import { deliveriesApi } from "@/services/api";
import type { DeliveryIn, DeliveryOut } from "@/types/api";

export interface DeliveryEditModalProps {
  delivery: DeliveryOut;
  open: boolean;
  onClose: () => void;
}

interface FormState {
  address_raw: string;
  district: string;
  microdistrict: string;
  street: string;
  house: string;
  apartment: string;
  entrance: string;
  floor: string;
  landmark: string;
  recipient_name: string;
  recipient_phone: string;
  courier_comment: string;
}

function toFormState(delivery: DeliveryOut): FormState {
  return {
    address_raw: delivery.address_raw ?? "",
    district: delivery.district ?? "",
    microdistrict: delivery.microdistrict ?? "",
    street: delivery.street ?? "",
    house: delivery.house ?? "",
    apartment: delivery.apartment ?? "",
    entrance: delivery.entrance ?? "",
    floor: delivery.floor ?? "",
    landmark: delivery.landmark ?? "",
    recipient_name: delivery.recipient_name ?? "",
    recipient_phone: delivery.recipient_phone ?? "",
    courier_comment: delivery.courier_comment ?? "",
  };
}

/** Blank strings are omitted so we don't overwrite optional fields with "". */
function toPayload(form: FormState): DeliveryIn {
  const payload: DeliveryIn = {};
  for (const [key, value] of Object.entries(form) as [keyof FormState, string][]) {
    payload[key] = value.trim() === "" ? null : value;
  }
  return payload;
}

/** "Редактировать адрес и получателя" — PATCH /deliveries/{id}. */
export function DeliveryEditModal({ delivery, open, onClose }: DeliveryEditModalProps) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(() => toFormState(delivery));
  // Re-populate the form whenever the modal (re)opens for this (possibly updated) delivery —
  // adjusted during render instead of an effect (react.dev/learn/you-might-not-need-an-effect).
  const [lastOpenKey, setLastOpenKey] = useState<string | null>(null);
  const openKey = open ? `${delivery.id}:${delivery.updated_at}` : null;
  if (openKey !== null && openKey !== lastOpenKey) {
    setLastOpenKey(openKey);
    setForm(toFormState(delivery));
  }

  const mutation = useMutation({
    mutationFn: (body: DeliveryIn) => deliveriesApi.update(delivery.id, body),
    onSuccess: () => {
      toast.success("Доставка обновлена");
      queryClient.invalidateQueries({ queryKey: queryKeys.deliveries.lists });
      queryClient.invalidateQueries({ queryKey: queryKeys.deliveries.detail(delivery.id) });
      onClose();
    },
    onError: (error) => toast.apiError(error),
  });

  const set = (key: keyof FormState) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setForm((current) => ({ ...current, [key]: event.target.value }));

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Адрес и получатель"
      size="lg"
      dismissible={!mutation.isPending}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>
            Отмена
          </Button>
          <Button onClick={() => mutation.mutate(toPayload(form))} loading={mutation.isPending}>
            Сохранить
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate(toPayload(form));
        }}
      >
        <Input label="Адрес (как написал клиент)" value={form.address_raw} onChange={set("address_raw")} />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Input label="Район" value={form.district} onChange={set("district")} />
          <Input label="Микрорайон" value={form.microdistrict} onChange={set("microdistrict")} />
          <Input label="Улица" value={form.street} onChange={set("street")} />
          <Input label="Дом" value={form.house} onChange={set("house")} />
          <Input label="Квартира" value={form.apartment} onChange={set("apartment")} />
          <Input label="Подъезд" value={form.entrance} onChange={set("entrance")} />
          <Input label="Этаж" value={form.floor} onChange={set("floor")} />
          <Input label="Ориентир" value={form.landmark} onChange={set("landmark")} />
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Input label="Получатель" value={form.recipient_name} onChange={set("recipient_name")} />
          <Input
            label="Телефон получателя"
            type="tel"
            value={form.recipient_phone}
            onChange={set("recipient_phone")}
          />
        </div>
        <Input label="Комментарий для курьера" value={form.courier_comment} onChange={set("courier_comment")} />
      </form>
    </Modal>
  );
}
