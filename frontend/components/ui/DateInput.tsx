"use client";

import clsx from "clsx";
import { useId, type ComponentProps, type ReactNode } from "react";

import { controlBaseClasses, controlStateClasses, describedBy, Field } from "@/components/ui/Field";

interface BaseProps extends Omit<ComponentProps<"input">, "type" | "value" | "onChange" | "size"> {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  containerClassName?: string;
}

export interface DateInputProps extends BaseProps {
  /** `"YYYY-MM-DD"` or empty string. */
  value: string;
  onValueChange: (value: string) => void;
  /** `"YYYY-MM-DD"` */
  min?: string;
  /** `"YYYY-MM-DD"` */
  max?: string;
}

/** Native date picker; value is always ISO `YYYY-MM-DD` (displayed per browser locale). */
export function DateInput({
  id,
  label,
  hint,
  error,
  value,
  onValueChange,
  className,
  containerClassName,
  required,
  "aria-describedby": ariaDescribedBy,
  ...props
}: DateInputProps) {
  const autoId = useId();
  const inputId = id ?? autoId;
  return (
    <Field id={inputId} label={label} hint={hint} error={error} required={required} className={containerClassName}>
      <input
        id={inputId}
        type="date"
        lang="ru"
        value={value}
        onChange={(event) => onValueChange(event.target.value)}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(inputId, hint, error, ariaDescribedBy)}
        className={clsx(controlBaseClasses, controlStateClasses(Boolean(error)), "h-10", className)}
        {...props}
      />
    </Field>
  );
}

export interface TimeInputProps extends BaseProps {
  /** `"HH:MM"` or empty string. */
  value: string;
  onValueChange: (value: string) => void;
}

/** Native 24h time input; value `HH:MM`. */
export function TimeInput({
  id,
  label,
  hint,
  error,
  value,
  onValueChange,
  className,
  containerClassName,
  required,
  step = 300,
  "aria-describedby": ariaDescribedBy,
  ...props
}: TimeInputProps) {
  const autoId = useId();
  const inputId = id ?? autoId;
  return (
    <Field id={inputId} label={label} hint={hint} error={error} required={required} className={containerClassName}>
      <input
        id={inputId}
        type="time"
        lang="ru"
        step={step}
        value={value}
        onChange={(event) => onValueChange(event.target.value.slice(0, 5))}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(inputId, hint, error, ariaDescribedBy)}
        className={clsx(controlBaseClasses, controlStateClasses(Boolean(error)), "h-10", className)}
        {...props}
      />
    </Field>
  );
}
