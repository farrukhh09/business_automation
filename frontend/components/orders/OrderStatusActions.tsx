"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Textarea } from "@/components/ui/Textarea";
import { useToast } from "@/components/ui/Toast";
import { formatOrderNumber } from "@/lib/format";
import { ORDER_STATUS_ACTION_LABELS } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { ordersApi } from "@/services/api";
import type { OrderDetail, OrderStatus } from "@/types/api";

export interface OrderStatusActionsProps {
  order: OrderDetail;
}

/**
 * Status action bar built only from `OrderDetail.allowed_transitions` (already filtered by role
 * and delivery type on the backend) — every entry becomes a button calling `POST /orders/{id}/status`,
 * except `CANCELLED`, which opens a confirm dialog asking for an optional reason and calls
 * `POST /orders/{id}/cancel`.
 */
export function OrderStatusActions({ order }: OrderStatusActionsProps) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [cancelOpen, setCancelOpen] = useState(false);
  const [reason, setReason] = useState("");

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.orders.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
  };

  const statusMutation = useMutation({
    mutationFn: (status: OrderStatus) => ordersApi.changeStatus(order.id, { status }),
    onSuccess: () => {
      toast.success("Статус заказа изменён");
      invalidate();
    },
    onError: (error) => toast.apiError(error),
  });

  const cancelMutation = useMutation({
    mutationFn: () => ordersApi.cancel(order.id, reason.trim() ? { reason: reason.trim() } : {}),
    onSuccess: () => {
      toast.success("Заказ отменён");
      setCancelOpen(false);
      setReason("");
      invalidate();
    },
    onError: (error) => toast.apiError(error),
  });

  if (order.allowed_transitions.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-sm font-medium text-slate-700">Статус:</span>
      {order.allowed_transitions.map((status) =>
        status === "CANCELLED" ? (
          <Button
            key={status}
            variant="danger"
            size="sm"
            onClick={() => setCancelOpen(true)}
            disabled={statusMutation.isPending}
          >
            {ORDER_STATUS_ACTION_LABELS.CANCELLED}
          </Button>
        ) : (
          <Button
            key={status}
            variant="outline"
            size="sm"
            onClick={() => statusMutation.mutate(status)}
            loading={statusMutation.isPending && statusMutation.variables === status}
            disabled={statusMutation.isPending}
          >
            {ORDER_STATUS_ACTION_LABELS[status]}
          </Button>
        ),
      )}

      <ConfirmDialog
        open={cancelOpen}
        title="Отменить заказ?"
        description={`Заказ ${formatOrderNumber(order.id)} будет отменён. Это действие нельзя отменить обратно из интерфейса.`}
        confirmLabel="Отменить заказ"
        cancelLabel="Не отменять"
        tone="danger"
        loading={cancelMutation.isPending}
        onConfirm={() => cancelMutation.mutate()}
        onCancel={() => {
          if (!cancelMutation.isPending) setCancelOpen(false);
        }}
      >
        <Textarea
          label="Причина отмены"
          hint="Необязательно"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={3}
        />
      </ConfirmDialog>
    </div>
  );
}
