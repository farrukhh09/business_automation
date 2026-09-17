"use client";

import clsx from "clsx";
import { useId, type ComponentProps, type ReactNode } from "react";

import { controlBaseClasses, controlStateClasses, describedBy, Field } from "@/components/ui/Field";

export interface SelectOptionItem {
  value: string | number;
  label: string;
  disabled?: boolean;
}

export interface SelectProps extends Omit<ComponentProps<"select">, "size"> {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  options?: readonly SelectOptionItem[];
  /** Adds an empty first option (value ""). */
  placeholder?: string;
  containerClassName?: string;
}

export function Select({
  id,
  label,
  hint,
  error,
  options,
  placeholder,
  className,
  containerClassName,
  required,
  children,
  "aria-describedby": ariaDescribedBy,
  ...props
}: SelectProps) {
  const autoId = useId();
  const selectId = id ?? autoId;
  return (
    <Field id={selectId} label={label} hint={hint} error={error} required={required} className={containerClassName}>
      <div className="relative">
        <select
          id={selectId}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(selectId, hint, error, ariaDescribedBy)}
          className={clsx(
            controlBaseClasses,
            controlStateClasses(Boolean(error)),
            "h-10 appearance-none pr-9",
            className,
          )}
          {...props}
        >
          {placeholder !== undefined ? <option value="">{placeholder}</option> : null}
          {options?.map((option) => (
            <option key={String(option.value)} value={option.value} disabled={option.disabled}>
              {option.label}
            </option>
          ))}
          {children}
        </select>
        <svg
          aria-hidden
          viewBox="0 0 20 20"
          fill="currentColor"
          className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-slate-400"
        >
          <path d="M5.23 7.21a.75.75 0 0 1 1.06.02L10 11.17l3.71-3.94a.75.75 0 1 1 1.08 1.04l-4.25 4.5a.75.75 0 0 1-1.08 0l-4.25-4.5a.75.75 0 0 1 .02-1.06Z" />
        </svg>
      </div>
    </Field>
  );
}
