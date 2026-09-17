"use client";

import clsx from "clsx";
import { useId, useState, type KeyboardEvent, type ReactNode } from "react";

import { Badge } from "@/components/ui/Badge";

export interface TagsInputProps {
  value: string[];
  onChange: (value: string[]) => void;
  label?: ReactNode;
  hint?: ReactNode;
  placeholder?: string;
  disabled?: boolean;
  id?: string;
  className?: string;
}

/**
 * Simple comma-separated tag/chip input (product aliases, FAQ keywords).
 * Enter or "," commits the current text as one or more tags; Backspace on an
 * empty field removes the last tag; existing tags can be removed individually.
 */
export function TagsInput({
  value,
  onChange,
  label,
  hint,
  placeholder = "Добавить и нажать Enter",
  disabled,
  id,
  className,
}: TagsInputProps) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const [draft, setDraft] = useState("");

  const commit = (raw: string) => {
    const parts = raw
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean);
    if (parts.length === 0) return;
    const next = [...value];
    for (const part of parts) {
      if (!next.includes(part)) next.push(part);
    }
    onChange(next);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit(draft);
      setDraft("");
    } else if (event.key === "Backspace" && draft === "" && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  };

  const onBlur = () => {
    if (draft.trim()) {
      commit(draft);
      setDraft("");
    }
  };

  const remove = (tag: string) => onChange(value.filter((item) => item !== tag));

  return (
    <div className={clsx("flex flex-col gap-1.5", className)}>
      {label ? (
        <label htmlFor={inputId} className="text-sm font-medium text-slate-700">
          {label}
        </label>
      ) : null}
      <div
        className={clsx(
          "flex min-h-10 flex-wrap items-center gap-1.5 rounded-md border border-slate-300 bg-white px-2 py-1.5 shadow-xs transition focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-500/25",
          disabled && "cursor-not-allowed bg-slate-50",
        )}
      >
        {value.map((tag) => (
          <Badge key={tag} tone="gray" className="gap-1 py-1">
            {tag}
            {!disabled ? (
              <button
                type="button"
                onClick={() => remove(tag)}
                aria-label={`Удалить «${tag}»`}
                className="-mr-0.5 ml-0.5 text-slate-400 hover:text-slate-700"
              >
                ×
              </button>
            ) : null}
          </Badge>
        ))}
        <input
          id={inputId}
          type="text"
          value={draft}
          disabled={disabled}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          onBlur={onBlur}
          placeholder={value.length === 0 ? placeholder : undefined}
          className="min-w-32 flex-1 border-0 bg-transparent p-1 text-sm text-slate-900 outline-none placeholder:text-slate-400 disabled:cursor-not-allowed"
        />
      </div>
      {hint ? <p className="text-sm text-slate-500">{hint}</p> : null}
    </div>
  );
}
