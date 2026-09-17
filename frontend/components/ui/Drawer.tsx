"use client";

import clsx from "clsx";
import { useId, useRef, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

import { useDialogBehavior, useIsClient } from "@/components/ui/useDialog";

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  /** Visible title; when omitted pass `ariaLabel`. */
  title?: ReactNode;
  ariaLabel?: string;
  side?: "left" | "right";
  children?: ReactNode;
  footer?: ReactNode;
  /** Tailwind width classes, default `w-full max-w-md`. */
  widthClassName?: string;
  initialFocusRef?: RefObject<HTMLElement | null>;
  /** Hide the built-in header (render your own inside children). */
  hideHeader?: boolean;
  className?: string;
  /** Classes for the fixed overlay container (e.g. `lg:hidden`). */
  containerClassName?: string;
}

export function Drawer({
  open,
  onClose,
  title,
  ariaLabel,
  side = "right",
  children,
  footer,
  widthClassName = "w-full max-w-md",
  initialFocusRef,
  hideHeader = false,
  className,
  containerClassName,
}: DrawerProps) {
  const isClient = useIsClient();
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useDialogBehavior({ open, onClose, panelRef, initialFocusRef });

  if (!open || !isClient) return null;

  const labelledBy = title && !hideHeader ? titleId : undefined;

  return createPortal(
    <div className={clsx("fixed inset-0 z-50", containerClassName)}>
      <div aria-hidden className="absolute inset-0 bg-slate-900/50" onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-label={labelledBy ? undefined : ariaLabel}
        tabIndex={-1}
        className={clsx(
          "absolute inset-y-0 flex flex-col bg-white shadow-xl outline-none",
          side === "left" ? "left-0" : "right-0",
          widthClassName,
          className,
        )}
      >
        {!hideHeader ? (
          <div className="flex items-center justify-between gap-4 border-b border-slate-100 px-4 py-3">
            <h2 id={titleId} className="min-w-0 truncate text-base font-semibold text-slate-900">
              {title}
            </h2>
            <button
              type="button"
              onClick={onClose}
              className="rounded-md p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 focus-visible:outline-2 focus-visible:outline-brand-600"
              aria-label="Закрыть"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-5" aria-hidden>
                <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
              </svg>
            </button>
          </div>
        ) : null}
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        {footer ? <div className="border-t border-slate-100 px-4 py-3">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}
