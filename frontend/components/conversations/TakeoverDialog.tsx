"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Textarea } from "@/components/ui/Textarea";

export interface TakeoverDialogProps {
  loading?: boolean;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}

/**
 * "Взять на себя": optional free-text reason, then POST /conversations/{id}/handoff.
 * Render only while open (see ConversationDetailView), so `reason` always starts fresh.
 */
export function TakeoverDialog({ loading = false, onConfirm, onCancel }: TakeoverDialogProps) {
  const [reason, setReason] = useState("");

  return (
    <Modal
      open
      onClose={() => {
        if (!loading) onCancel();
      }}
      title="Взять диалог на себя"
      description="Бот перестанет отвечать автоматически, пока диалог не будет возвращён."
      size="sm"
      dismissible={!loading}
      footer={
        <>
          <Button variant="outline" onClick={onCancel} disabled={loading}>
            Отмена
          </Button>
          <Button onClick={() => onConfirm(reason.trim())} loading={loading}>
            Взять на себя
          </Button>
        </>
      }
    >
      <Textarea
        label="Причина (необязательно)"
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        rows={3}
        placeholder="Например: клиент попросил оператора"
      />
    </Modal>
  );
}
