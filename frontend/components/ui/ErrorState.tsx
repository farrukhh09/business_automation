"use client";

import clsx from "clsx";

import { Button } from "@/components/ui/Button";
import { IconAlert } from "@/components/ui/icons";
import { getErrorMessage } from "@/services/http";

export interface ErrorStateProps {
  error: unknown;
  title?: string;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
}

/** Error block with `ApiError.detail` and a retry button (07 §5). */
export function ErrorState({ error, title = "Не удалось загрузить данные", onRetry, retrying, className }: ErrorStateProps) {
  return (
    <div role="alert" className={clsx("flex flex-col items-center justify-center px-4 py-10 text-center", className)}>
      <div className="mb-3 flex size-12 items-center justify-center rounded-full bg-red-50 text-red-500" aria-hidden>
        <IconAlert className="size-6" />
      </div>
      <h3 className="text-base font-semibold text-slate-900">{title}</h3>
      <p className="mt-1 max-w-md text-sm text-slate-600">{getErrorMessage(error)}</p>
      {onRetry ? (
        <Button variant="outline" className="mt-4" onClick={onRetry} loading={retrying}>
          Повторить
        </Button>
      ) : null}
    </div>
  );
}
