"use client";

import clsx from "clsx";
import { useId, type ComponentProps, type ReactNode } from "react";

import { controlBaseClasses, controlStateClasses, describedBy, Field } from "@/components/ui/Field";

export interface InputProps extends Omit<ComponentProps<"input">, "size" | "prefix"> {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  /** Text/icon shown inside the field on the left. */
  prefix?: ReactNode;
  /** Text shown inside the field on the right, e.g. "сомони". */
  suffix?: ReactNode;
  containerClassName?: string;
}

export function Input({
  id,
  label,
  hint,
  error,
  prefix,
  suffix,
  className,
  containerClassName,
  required,
  "aria-describedby": ariaDescribedBy,
  ...props
}: InputProps) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const input = (
    <input
      id={inputId}
      required={required}
      aria-invalid={error ? true : undefined}
      aria-describedby={describedBy(inputId, hint, error, ariaDescribedBy)}
      className={clsx(
        controlBaseClasses,
        controlStateClasses(Boolean(error)),
        "h-10",
        prefix ? "pl-9" : null,
        suffix ? "pr-16" : null,
        className,
      )}
      {...props}
    />
  );

  return (
    <Field id={inputId} label={label} hint={hint} error={error} required={required} className={containerClassName}>
      {prefix || suffix ? (
        <div className="relative">
          {prefix ? (
            <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-slate-400">
              {prefix}
            </span>
          ) : null}
          {input}
          {suffix ? (
            <span className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-3 text-sm text-slate-500">
              {suffix}
            </span>
          ) : null}
        </div>
      ) : (
        input
      )}
    </Field>
  );
}
