"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { MapView, type MapMarker } from "@/components/shared";
import { formatCoordinates } from "@/lib/format";
import { queryKeys } from "@/lib/query";
import { deliveriesApi } from "@/services/api";
import type { DeliveryOut } from "@/types/api";

export interface MapPointModalProps {
  delivery: DeliveryOut;
  open: boolean;
  onClose: () => void;
  /** Map centre when the delivery has no point yet. */
  fallbackCenter: { lat: number; lng: number };
}

const MARKER_ID = "point";

/** "Поставить точку на карте" — click/drag → PATCH latitude/longitude (location_source=OPERATOR, geocode_status=MANUAL). */
export function MapPointModal({ delivery, open, onClose, fallbackCenter }: MapPointModalProps) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [point, setPoint] = useState<{ lat: number; lng: number } | null>(null);
  // Re-seed the point whenever the modal (re)opens for this delivery's current coordinates —
  // adjusted during render instead of an effect (react.dev/learn/you-might-not-need-an-effect).
  const [lastOpenKey, setLastOpenKey] = useState<string | null>(null);
  const openKey = open ? `${delivery.id}:${delivery.latitude ?? ""}:${delivery.longitude ?? ""}` : null;
  if (openKey !== null && openKey !== lastOpenKey) {
    setLastOpenKey(openKey);
    setPoint(
      delivery.latitude !== null && delivery.longitude !== null
        ? { lat: delivery.latitude, lng: delivery.longitude }
        : null,
    );
  }

  const mutation = useMutation({
    mutationFn: (value: { lat: number; lng: number }) =>
      deliveriesApi.update(delivery.id, { latitude: value.lat, longitude: value.lng }),
    onSuccess: () => {
      toast.success("Точка сохранена");
      queryClient.invalidateQueries({ queryKey: queryKeys.deliveries.lists });
      queryClient.invalidateQueries({ queryKey: queryKeys.deliveries.detail(delivery.id) });
      onClose();
    },
    onError: (error) => toast.apiError(error),
  });

  const markers: MapMarker[] = point
    ? [{ id: MARKER_ID, lat: point.lat, lng: point.lng, tone: "selected", label: delivery.address_formatted ?? delivery.address_raw }]
    : [];

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Точка на карте"
      description={delivery.address_formatted ?? delivery.address_raw}
      size="lg"
      dismissible={!mutation.isPending}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>
            Отмена
          </Button>
          <Button onClick={() => point && mutation.mutate(point)} loading={mutation.isPending} disabled={!point}>
            Сохранить точку
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <p className="text-sm text-slate-500">
          Нажмите на карту, чтобы поставить точку, или перетащите существующую метку.
        </p>
        <MapView
          center={point ?? fallbackCenter}
          markers={markers}
          onMapClick={(lat, lng) => setPoint({ lat, lng })}
          draggableMarkerId={point ? MARKER_ID : null}
          onMarkerDrag={(_id, lat, lng) => setPoint({ lat, lng })}
          height={360}
        />
        <p className="text-sm text-slate-500">
          Координаты: {point ? formatCoordinates(point.lat, point.lng) : "не выбраны"}
        </p>
      </div>
    </Modal>
  );
}
