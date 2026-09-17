"use client";

import clsx from "clsx";
import { useId, type ComponentProps, type ReactNode } from "react";

export interface CheckboxProps extends Omit<ComponentProps<"input">, "type"> {
  label?: ReactNode;
  description?: ReactNode;
  error?: ReactNode;
  containerClassName?: string;
}

export function Checkbox({ id, label, description, error, className, containerClassName, ...props }: CheckboxProps) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const descriptionId = description ? `${inputId}-description` : undefined;
  const errorId = error ? `${inputId}-error` : undefined;
  return (
    <div className={clsx("flex items-start gap-3", containerClassName)}>
      <input
        id={inputId}
        type="checkbox"
        aria-invalid={error ? true : undefined}
        aria-describedby={[descriptionId, errorId].filter(Boolean).join(" ") || undefined}
        className={clsx(
          "mt-0.5 size-4 shrink-0 cursor-pointer rounded border-slate-300 accent-brand-600",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600",
          "disabled:cursor-not-allowed disabled:opacity-60",
          className,
        )}
        {...props}
      />
      {label || description || error ? (
        <div className="min-w-0 text-sm">
          {label ? (
            <label htmlFor={inputId} className="cursor-pointer font-medium text-slate-800">
              {label}
            </label>
          ) : null}
          {description ? (
            <p id={descriptionId} className="text-slate-500">
              {description}
            </p>
          ) : null}
          {error ? (
            <p id={errorId} className="text-red-600">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export interface SwitchProps extends Omit<ComponentProps<"button">, "onChange" | "type" | "role"> {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  label?: ReactNode;
  description?: ReactNode;
  containerClassName?: string;
}

/** On/off toggle (`role="switch"`), e.g. product active flag. */
export function Switch({
  id,
  checked,
  onCheckedChange,
  label,
  description,
  disabled,
  className,
  containerClassName,
  ...props
}: SwitchProps) {
  const autoId = useId();
  const switchId = id ?? autoId;
  const labelId = label ? `${switchId}-label` : undefined;
  const descriptionId = description ? `${switchId}-description` : undefined;
  return (
    <div className={clsx("flex items-center gap-3", containerClassName)}>
      <button
        id={switchId}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={labelId}
        aria-describedby={descriptionId}
        disabled={disabled}
        onClick={() => onCheckedChange(!checked)}
        className={clsx(
          "relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full transition-colors",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600",
          "disabled:cursor-not-allowed disabled:opacity-60",
          checked ? "bg-brand-600" : "bg-slate-300",
          className,
        )}
        {...props}
      >
        <span
          aria-hidden
          className={clsx(
            "inline-block size-5 rounded-full bg-white shadow-sm ring-0 transition-transform",
            checked ? "translate-x-5" : "translate-x-0.5",
          )}
        />
      </button>
      {label || description ? (
        <div className="min-w-0 text-sm">
          {label ? (
            <span id={labelId} className="font-medium text-slate-800">
              {label}
            </span>
          ) : null}
          {description ? (
            <p id={descriptionId} className="text-slate-500">
              {description}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
