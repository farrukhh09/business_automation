"use client";

import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Textarea";
import type { DeliveryIn } from "@/types/api";

export interface DeliveryAddressFieldsProps {
  value: DeliveryIn;
  onChange: (value: DeliveryIn) => void;
  errors?: Partial<Record<"address" | "recipient_name" | "phone", string>>;
  disabled?: boolean;
}

function textValue(value: DeliveryIn, key: keyof DeliveryIn): string {
  const raw = value[key];
  return typeof raw === "string" ? raw : "";
}

/**
 * Structured delivery address block (`DeliveryIn`, 04-api.md §5): address_raw, district,
 * microdistrict, street, house, apartment, entrance, floor, landmark, recipient, courier comment.
 * Used by both the new-order form and the order edit mode (ADMIN and OPERATOR).
 */
export function DeliveryAddressFields({ value, onChange, errors, disabled = false }: DeliveryAddressFieldsProps) {
  const set = (patch: Partial<DeliveryIn>) => onChange({ ...value, ...patch });

  return (
    <div className="flex flex-col gap-4">
      <Textarea
        label="Адрес"
        placeholder="Улица, дом, ориентир…"
        value={textValue(value, "address_raw")}
        onChange={(event) => set({ address_raw: event.target.value })}
        error={errors?.address}
        disabled={disabled}
        rows={2}
      />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Input
          label="Район"
          value={textValue(value, "district")}
          onChange={(event) => set({ district: event.target.value })}
          disabled={disabled}
        />
        <Input
          label="Микрорайон"
          value={textValue(value, "microdistrict")}
          onChange={(event) => set({ microdistrict: event.target.value })}
          disabled={disabled}
        />
        <Input
          label="Улица"
          value={textValue(value, "street")}
          onChange={(event) => set({ street: event.target.value })}
          disabled={disabled}
        />
        <Input
          label="Дом"
          value={textValue(value, "house")}
          onChange={(event) => set({ house: event.target.value })}
          disabled={disabled}
        />
        <Input
          label="Квартира"
          value={textValue(value, "apartment")}
          onChange={(event) => set({ apartment: event.target.value })}
          disabled={disabled}
        />
        <Input
          label="Подъезд"
          value={textValue(value, "entrance")}
          onChange={(event) => set({ entrance: event.target.value })}
          disabled={disabled}
        />
        <Input
          label="Этаж"
          value={textValue(value, "floor")}
          onChange={(event) => set({ floor: event.target.value })}
          disabled={disabled}
        />
        <Input
          label="Ориентир"
          value={textValue(value, "landmark")}
          onChange={(event) => set({ landmark: event.target.value })}
          disabled={disabled}
        />
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Input
          label="Получатель"
          value={textValue(value, "recipient_name")}
          onChange={(event) => set({ recipient_name: event.target.value })}
          error={errors?.recipient_name}
          disabled={disabled}
        />
        <Input
          label="Телефон получателя"
          type="tel"
          value={textValue(value, "recipient_phone")}
          onChange={(event) => set({ recipient_phone: event.target.value })}
          error={errors?.phone}
          disabled={disabled}
        />
      </div>
      <Textarea
        label="Комментарий курьеру"
        value={textValue(value, "courier_comment")}
        onChange={(event) => set({ courier_comment: event.target.value })}
        disabled={disabled}
        rows={2}
      />
    </div>
  );
}
