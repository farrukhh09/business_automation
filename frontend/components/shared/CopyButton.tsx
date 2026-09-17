"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

import { Button, type ButtonProps } from "@/components/ui/Button";
import { IconCopy } from "@/components/ui/icons";
import { useToast } from "@/components/ui/Toast";
import { copyText } from "@/lib/clipboard";

export interface CopyButtonProps extends Omit<ButtonProps, "onClick" | "children" | "leftIcon"> {
  /** Text to copy, or a function producing it at click time. */
  text: string | (() => string);
  /** Button caption (default "Копировать"). */
  label?: ReactNode;
  /** Caption for ~2 s after a successful copy (default "Скопировано"). */
  copiedLabel?: ReactNode;
  /** Success toast title; `null` → no toast (default "Скопировано в буфер обмена"). */
  successMessage?: string | null;
  /** Error toast title (default "Не удалось скопировать"). */
  errorMessage?: string;
  /** Icon-only square button; `label` becomes the aria-label. */
  iconOnly?: boolean;
  onCopied?: () => void;
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-4" aria-hidden>
      <path d="M5 12.5l4.5 4.5L19 7.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Copies text to the clipboard (lib/clipboard) and confirms with a toast. */
export function CopyButton({
  text,
  label = "Копировать",
  copiedLabel = "Скопировано",
  successMessage = "Скопировано в буфер обмена",
  errorMessage = "Не удалось скопировать",
  iconOnly = false,
  onCopied,
  variant = "outline",
  size = "sm",
  disabled,
  ...props
}: CopyButtonProps) {
  const toast = useToast();
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );

  const handleClick = async () => {
    const value = typeof text === "function" ? text() : text;
    const ok = value ? await copyText(value) : false;
    if (!ok) {
      toast.error(errorMessage, value ? { description: "Выделите текст и скопируйте вручную." } : undefined);
      return;
    }
    if (successMessage) toast.success(successMessage);
    onCopied?.();
    setCopied(true);
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setCopied(false), 2000);
  };

  const icon = copied ? <CheckIcon /> : <IconCopy className="size-4" />;
  const caption = copied ? copiedLabel : label;

  if (iconOnly) {
    return (
      <Button
        variant={variant}
        size="icon"
        onClick={handleClick}
        disabled={disabled}
        aria-label={typeof label === "string" ? label : "Копировать"}
        title={typeof label === "string" ? label : undefined}
        {...props}
      >
        {icon}
      </Button>
    );
  }

  return (
    <Button variant={variant} size={size} onClick={handleClick} disabled={disabled} leftIcon={icon} {...props}>
      <span aria-live="polite">{caption}</span>
    </Button>
  );
}
