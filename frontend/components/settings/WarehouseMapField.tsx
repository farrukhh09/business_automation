"use client";

import { MapView } from "@/components/shared";
import { formatCoordinates } from "@/lib/format";

export interface WarehouseMapFieldProps {
  latitude: number | null;
  longitude: number | null;
  onChange: (lat: number, lng: number) => void;
  disabled?: boolean;
}

/** Khujand city centre — used as the initial map view before a point is set. */
const DEFAULT_CENTER: [number, number] = [40.2842191, 69.6191174];

/** Click-or-drag map picker for the warehouse (kitchen/pickup) point, backed by the shared MapView. */
export function WarehouseMapField({ latitude, longitude, onChange, disabled = false }: WarehouseMapFieldProps) {
  const hasPoint = latitude !== null && longitude !== null;
  const center: [number, number] = hasPoint ? [latitude as number, longitude as number] : DEFAULT_CENTER;

  return (
    <div className="flex flex-col gap-2">
      <span className="text-sm font-medium text-slate-700">Точка склада на карте</span>
      <p className="text-sm text-slate-500">
        {disabled
          ? "Изменение координат доступно только администратору."
          : "Кликните по карте или перетащите маркер, чтобы задать точку склада."}
      </p>
      <MapView
        center={center}
        zoom={hasPoint ? 15 : 12}
        markers={hasPoint ? [{ id: "warehouse", lat: latitude as number, lng: longitude as number, tone: "warehouse", label: "Склад" }] : []}
        onMapClick={disabled ? undefined : (lat, lng) => onChange(lat, lng)}
        draggableMarkerId={!disabled && hasPoint ? "warehouse" : null}
        onMarkerDrag={disabled ? undefined : (_id, lat, lng) => onChange(lat, lng)}
        height={360}
        ariaLabel="Карта для выбора точки склада"
      />
      <p className="text-sm text-slate-600">
        Координаты: <span className="font-medium text-slate-900">{formatCoordinates(latitude, longitude)}</span>
      </p>
    </div>
  );
}
