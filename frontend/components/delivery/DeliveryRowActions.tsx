"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { CandidateModal } from "@/components/delivery/CandidateModal";
import { DeliveryEditModal } from "@/components/delivery/DeliveryEditModal";
import { DispatchModal } from "@/components/delivery/DispatchModal";
import { LocationLinkModal } from "@/components/delivery/LocationLinkModal";
import { MapPointModal } from "@/components/delivery/MapPointModal";
import { IconMapPin, IconRefresh } from "@/components/ui/icons";
import { queryKeys } from "@/lib/query";
import { deliveriesApi } from "@/services/api";
import type { DeliveryListItem, DispatchResultOut, LocationLinkOut } from "@/types/api";

export interface DeliveryRowActionsProps {
  delivery: DeliveryListItem;
  /** Fallback map centre for "поставить точку на карте" (склад, иначе центр города). */
  mapCenter: { lat: number; lng: number };
}

type ModalKind = "edit" | "candidate" | "map" | null;

/** Row-level actions for one delivery: edit, geocode, candidates, map point, client link, dispatch. */
export function DeliveryRowActions({ delivery, mapCenter }: DeliveryRowActionsProps) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [modal, setModal] = useState<ModalKind>(null);
  const [linkResult, setLinkResult] = useState<LocationLinkOut | null>(null);
  const [linkModalOpen, setLinkModalOpen] = useState(false);
  const [dispatchResult, setDispatchResult] = useState<DispatchResultOut | null>(null);
  const [dispatchModalOpen, setDispatchModalOpen] = useState(false);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.deliveries.lists });
    queryClient.invalidateQueries({ queryKey: queryKeys.deliveries.detail(delivery.id) });
  };

  const geocodeMutation = useMutation({
    mutationFn: () => deliveriesApi.geocode(delivery.id),
    onSuccess: () => {
      toast.success("Геокодирование запущено заново");
      invalidate();
    },
    onError: (error) => toast.apiError(error),
  });

  const linkMutation = useMutation({
    mutationFn: () => deliveriesApi.createLocationLink(delivery.id),
    onSuccess: (link) => {
      setLinkResult(link);
      setLinkModalOpen(true);
    },
    onError: (error) => toast.apiError(error),
  });

  const dispatchMutation = useMutation({
    mutationFn: () => deliveriesApi.dispatch(delivery.id),
    onSuccess: (result) => {
      setDispatchResult(result);
      setDispatchModalOpen(true);
      invalidate();
    },
    onError: (error) => toast.apiError(error),
  });

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="outline" size="sm" onClick={() => setModal("edit")}>
        Редактировать
      </Button>
      <Button
        variant="outline"
        size="sm"
        leftIcon={<IconRefresh className="size-4" />}
        onClick={() => geocodeMutation.mutate()}
        loading={geocodeMutation.isPending}
      >
        Перегеокодировать
      </Button>
      {delivery.geocode_status === "AMBIGUOUS" ? (
        <Button variant="outline" size="sm" onClick={() => setModal("candidate")}>
          Выбрать вариант
        </Button>
      ) : null}
      <Button
        variant="outline"
        size="sm"
        leftIcon={<IconMapPin className="size-4" />}
        onClick={() => setModal("map")}
      >
        Точка на карте
      </Button>
      <Button
        variant="outline"
        size="sm"
        onClick={() => linkMutation.mutate()}
        loading={linkMutation.isPending}
      >
        Ссылка клиенту
      </Button>
      <Button size="sm" onClick={() => dispatchMutation.mutate()} loading={dispatchMutation.isPending}>
        Передать в Maxim
      </Button>

      <DeliveryEditModal delivery={delivery} open={modal === "edit"} onClose={() => setModal(null)} />
      <CandidateModal delivery={delivery} open={modal === "candidate"} onClose={() => setModal(null)} />
      <MapPointModal
        delivery={delivery}
        open={modal === "map"}
        onClose={() => setModal(null)}
        fallbackCenter={mapCenter}
      />
      <LocationLinkModal open={linkModalOpen} onClose={() => setLinkModalOpen(false)} link={linkResult} />
      <DispatchModal open={dispatchModalOpen} onClose={() => setDispatchModalOpen(false)} result={dispatchResult} />
    </div>
  );
}
