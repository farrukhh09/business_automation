"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { GEO_PRECISION_LABELS } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { deliveriesApi } from "@/services/api";
import type { DeliveryOut, GeoPrecision } from "@/types/api";

export interface CandidateModalProps {
  delivery: DeliveryOut;
  open: boolean;
  onClose: () => void;
}

/** "Выбрать вариант адреса" — geocode_status=AMBIGUOUS → POST /deliveries/{id}/select-candidate. */
export function CandidateModal({ delivery, open, onClose }: CandidateModalProps) {
  const toast = useToast();
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: (index: number) => deliveriesApi.selectCandidate(delivery.id, { index }),
    onSuccess: () => {
      toast.success("Адрес уточнён");
      queryClient.invalidateQueries({ queryKey: queryKeys.deliveries.lists });
      queryClient.invalidateQueries({ queryKey: queryKeys.deliveries.detail(delivery.id) });
      onClose();
    },
    onError: (error) => toast.apiError(error),
  });

  return (
    <Modal open={open} onClose={onClose} title="Уточните адрес" description={delivery.address_raw} size="md">
      {delivery.geocode_candidates.length === 0 ? (
        <EmptyState title="Вариантов нет" description="Перегеокодируйте доставку, чтобы получить варианты адреса." />
      ) : (
        <ul className="flex flex-col gap-2">
          {delivery.geocode_candidates.map((candidate, index) => (
            <li
              key={`${candidate.lat}-${candidate.lng}-${index}`}
              className="flex flex-col gap-2 rounded-lg border border-slate-200 p-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium text-slate-900">{candidate.formatted}</p>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <Badge tone="gray">
                    {GEO_PRECISION_LABELS[candidate.precision as GeoPrecision] ?? candidate.precision}
                  </Badge>
                  <span className="text-xs text-slate-400 tabular-nums">
                    {candidate.lat.toFixed(5)}, {candidate.lng.toFixed(5)}
                  </span>
                </div>
              </div>
              <Button
                size="sm"
                variant="outline"
                onClick={() => mutation.mutate(index)}
                loading={mutation.isPending && mutation.variables === index}
                disabled={mutation.isPending}
              >
                Выбрать
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}
