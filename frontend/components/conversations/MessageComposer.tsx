"use client";

import { useState, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";

export interface MessageComposerProps {
  /** true when mode === "AI" — the operator must take the conversation over first. */
  disabled?: boolean;
  sending?: boolean;
  onSend: (text: string) => void;
  /** Shown above the composer, e.g. the 24h messaging-window explanation. Dismissible. */
  errorBanner?: string | null;
  onDismissError?: () => void;
}

/** Reply composer for the operator: Ctrl+Enter or the button submits. */
export function MessageComposer({ disabled = false, sending = false, onSend, errorBanner, onDismissError }: MessageComposerProps) {
  const [text, setText] = useState("");

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled || sending) return;
    onSend(trimmed);
    setText("");
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="flex flex-col gap-2 border-t border-slate-200 bg-white p-3 sm:p-4">
      {errorBanner ? (
        <div className="flex items-start justify-between gap-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-200 ring-inset">
          <span>{errorBanner}</span>
          {onDismissError ? (
            <button
              type="button"
              onClick={onDismissError}
              className="shrink-0 text-red-500 hover:text-red-700"
              aria-label="Скрыть сообщение об ошибке"
            >
              ✕
            </button>
          ) : null}
        </div>
      ) : null}

      {disabled ? (
        <p className="text-sm text-slate-500">Сначала возьмите диалог на себя, чтобы отвечать вручную.</p>
      ) : null}

      <div className="flex items-end gap-2">
        <Textarea
          aria-label="Ответ клиенту"
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled || sending}
          rows={2}
          placeholder={disabled ? "Диалог ведёт бот" : "Введите сообщение… (Ctrl+Enter — отправить)"}
          containerClassName="flex-1"
          className="resize-none"
        />
        <Button onClick={submit} disabled={disabled || !text.trim()} loading={sending}>
          Отправить
        </Button>
      </div>
    </div>
  );
}
