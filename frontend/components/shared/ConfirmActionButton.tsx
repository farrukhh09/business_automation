"use client";

import { useState, type ReactNode } from "react";

import { Button, type ButtonProps } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useToast } from "@/components/ui/Toast";

export interface ConfirmActionButtonProps extends Omit<ButtonProps, "onClick"> {
  /** Dialog title, e.g. "Удалить товар?". */
  confirmTitle: ReactNode;
  confirmDescription?: ReactNode;
  /** Confirm button caption (default "Подтвердить"). */
  confirmLabel?: string;
  cancelLabel?: string;
  /** Confirm button tone; default `danger` when `variant="danger"`, else `primary`. */
  confirmTone?: "primary" | "danger";
  /**
   * Runs on confirm. If it returns a Promise (e.g. `mutation.mutateAsync()`), the dialog shows a spinner,
   * closes when it resolves and stays open when it rejects.
   */
  onConfirm: () => unknown;
  /** External pending flag (e.g. `mutation.isPending`) for the confirm button. */
  confirmLoading?: boolean;
  /**
   * Show `toast.apiError` when the returned Promise rejects (default true).
   * Pass `false` if the mutation already shows its own error toast.
   */
  errorToast?: boolean;
  /** Extra content inside the dialog (e.g. a reason textarea). */
  dialogContent?: ReactNode;
  onOpenChange?: (open: boolean) => void;
}

function isPromiseLike(value: unknown): value is PromiseLike<unknown> {
  return typeof value === "object" && value !== null && typeof (value as PromiseLike<unknown>).then === "function";
}

/** Button that asks for confirmation (ConfirmDialog) before a destructive/important action. */
export function ConfirmActionButton({
  confirmTitle,
  confirmDescription,
  confirmLabel = "Подтвердить",
  cancelLabel,
  confirmTone,
  onConfirm,
  confirmLoading = false,
  errorToast = true,
  dialogContent,
  onOpenChange,
  variant = "outline",
  children,
  ...buttonProps
}: ConfirmActionButtonProps) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);

  const changeOpen = (next: boolean) => {
    setOpen(next);
    onOpenChange?.(next);
  };

  const handleConfirm = async () => {
    const result = onConfirm();
    if (!isPromiseLike(result)) {
      changeOpen(false);
      return;
    }
    setPending(true);
    try {
      await result;
      changeOpen(false);
    } catch (error) {
      if (errorToast) toast.apiError(error);
    } finally {
      setPending(false);
    }
  };

  const busy = pending || confirmLoading;

  return (
    <>
      <Button variant={variant} {...buttonProps} onClick={() => changeOpen(true)}>
        {children}
      </Button>
      <ConfirmDialog
        open={open}
        title={confirmTitle}
        description={confirmDescription}
        confirmLabel={confirmLabel}
        cancelLabel={cancelLabel}
        tone={confirmTone ?? (variant === "danger" ? "danger" : "primary")}
        loading={busy}
        onConfirm={handleConfirm}
        onCancel={() => {
          if (!busy) changeOpen(false);
        }}
      >
        {dialogContent}
      </ConfirmDialog>
    </>
  );
}
