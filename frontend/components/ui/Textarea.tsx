"use client";

import clsx from "clsx";
import { useId, type ComponentProps, type ReactNode } from "react";

import { controlBaseClasses, controlStateClasses, describedBy, Field } from "@/components/ui/Field";

export interface TextareaProps extends ComponentProps<"textarea"> {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  containerClassName?: string;
}

export function Textarea({
  id,
  label,
  hint,
  error,
  className,
  containerClassName,
  required,
  rows = 4,
  "aria-describedby": ariaDescribedBy,
  ...props
}: TextareaProps) {
  const autoId = useId();
  const textareaId = id ?? autoId;
  return (
    <Field id={textareaId} label={label} hint={hint} error={error} required={required} className={containerClassName}>
      <textarea
        id={textareaId}
        rows={rows}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(textareaId, hint, error, ariaDescribedBy)}
        className={clsx(controlBaseClasses, controlStateClasses(Boolean(error)), "py-2", className)}
        {...props}
      />
    </Field>
  );
}
