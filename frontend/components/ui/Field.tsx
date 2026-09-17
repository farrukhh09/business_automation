import clsx from "clsx";
import type { ReactNode } from "react";

export interface FieldProps {
  id: string;
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  className?: string;
  children: ReactNode;
}

export const fieldHintId = (id: string) => `${id}-hint`;
export const fieldErrorId = (id: string) => `${id}-error`;

/** aria-describedby value for a control inside <Field>. */
export function describedBy(id: string, hint?: ReactNode, error?: ReactNode, extra?: string): string | undefined {
  const ids = [extra, hint ? fieldHintId(id) : null, error ? fieldErrorId(id) : null].filter(Boolean);
  return ids.length ? ids.join(" ") : undefined;
}

/** Label + control + hint/error wrapper shared by form controls. */
export function Field({ id, label, hint, error, required, className, children }: FieldProps) {
  return (
    <div className={clsx("flex flex-col gap-1.5", className)}>
      {label ? (
        <label htmlFor={id} className="text-sm font-medium text-slate-700">
          {label}
          {required ? (
            <span className="ml-0.5 text-red-600" aria-hidden>
              *
            </span>
          ) : null}
        </label>
      ) : null}
      {children}
      {error ? (
        <p id={fieldErrorId(id)} className="text-sm text-red-600">
          {error}
        </p>
      ) : hint ? (
        <p id={fieldHintId(id)} className="text-sm text-slate-500">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export const controlBaseClasses =
  "block w-full rounded-md border bg-white px-3 text-sm text-slate-900 shadow-xs transition placeholder:text-slate-400 focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500";

export function controlStateClasses(invalid: boolean): string {
  return invalid
    ? "border-red-400 focus:border-red-500 focus:ring-red-500/25"
    : "border-slate-300 focus:border-brand-500 focus:ring-brand-500/25";
}
