"use client";

import { Button } from "@/components/ui/Button";
import { CopyButton } from "@/components/shared/CopyButton";
import { Modal } from "@/components/ui/Modal";
import { formatDateTime } from "@/lib/format";
import type { LocationLinkOut } from "@/types/api";

export interface LocationLinkModalProps {
  open: boolean;
  onClose: () => void;
  /** `null` while the request is in flight; the modal itself does not trigger it. */
  link: LocationLinkOut | null;
}

/** "Ссылка клиенту" — shows the URL from POST /deliveries/{id}/location-link. */
export function LocationLinkModal({ open, onClose, link }: LocationLinkModalProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Ссылка для клиента"
      description="Отправьте клиенту в Instagram, чтобы он отметил точку доставки на карте."
      size="sm"
      footer={
        <Button variant="outline" onClick={onClose}>
          Закрыть
        </Button>
      }
    >
      {link ? (
        <div className="flex flex-col gap-3">
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm break-all text-slate-700">
            {link.url}
          </div>
          <p className="text-sm text-slate-500">Действует до {formatDateTime(link.expires_at)}</p>
          <CopyButton text={link.url} label="Скопировать ссылку" fullWidth />
        </div>
      ) : null}
    </Modal>
  );
}
