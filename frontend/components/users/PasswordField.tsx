"use client";

import { useId, useState } from "react";

import { Checkbox, Input } from "@/components/ui";

export interface PasswordFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  hint?: string;
  required?: boolean;
  autoComplete?: string;
}

/** Password input with a "show password" checkbox toggle (no interactive control inside Input's suffix). */
export function PasswordField({ label, value, onChange, error, hint, required, autoComplete }: PasswordFieldProps) {
  const [show, setShow] = useState(false);
  const toggleId = useId();

  return (
    <div className="flex flex-col gap-2">
      <Input
        id={toggleId}
        label={label}
        type={show ? "text" : "password"}
        required={required}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        error={error}
        hint={!error ? hint : undefined}
        autoComplete={autoComplete}
      />
      <Checkbox
        checked={show}
        onChange={(event) => setShow(event.target.checked)}
        label="Показать пароль"
      />
    </div>
  );
}
