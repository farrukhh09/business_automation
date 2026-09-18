"use client";

import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";

/** Kept in step with the backend (`TestChatService`): JPEG, PNG, WebP, GIF, up to 10 MB. */
export const IMAGE_ACCEPT = "image/jpeg,image/png,image/webp,image/gif";
export const MAX_IMAGE_BYTES = 10 * 1024 * 1024;

export interface MessageComposerProps {
  /** true when mode === "AI" — the operator must take the conversation over first. */
  disabled?: boolean;
  sending?: boolean;
  onSend: (text: string) => void;
  /** Shown above the composer, e.g. the 24h messaging-window explanation. Dismissible. */
  errorBanner?: string | null;
  onDismissError?: () => void;
  /**
   * Test chat only (04 §11a): when set, a picture can be attached (📎, drag & drop or Ctrl+V) and
   * the text becomes its caption. The operator's reply to Instagram stays text-only.
   */
  onSendImage?: (file: File, text: string) => void;
  /** Called when the chosen file is not a picture or is too large; the page shows the message. */
  onImageRejected?: (message: string) => void;
}

function imageProblem(file: File): string | null {
  if (!IMAGE_ACCEPT.split(",").includes(file.type)) {
    return "Можно отправить только изображение: JPEG, PNG, WebP или GIF";
  }
  if (file.size > MAX_IMAGE_BYTES) return "Изображение слишком большое: максимум 10 МБ";
  if (file.size === 0) return "Файл пустой";
  return null;
}

/** Reply composer for the operator: Ctrl+Enter or the button submits. */
export function MessageComposer({
  disabled = false,
  sending = false,
  onSend,
  errorBanner,
  onDismissError,
  onSendImage,
  onImageRejected,
}: MessageComposerProps) {
  const [text, setText] = useState("");
  /** The attached picture together with the object URL of its thumbnail. */
  const [image, setImage] = useState<{ file: File; preview: string } | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const previewRef = useRef<string | null>(null);

  /* Object URLs are released as soon as they are replaced, and on unmount. */
  useEffect(() => () => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
  }, []);

  const attach = (file: File | null | undefined) => {
    if (!file) return;
    const problem = imageProblem(file);
    if (problem) {
      onImageRejected?.(problem);
      return;
    }
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    previewRef.current = URL.createObjectURL(file);
    setImage({ file, preview: previewRef.current });
  };

  const clearImage = () => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    previewRef.current = null;
    setImage(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const submit = () => {
    if (disabled || sending) return;
    const trimmed = text.trim();
    if (image && onSendImage) {
      onSendImage(image.file, trimmed);
      setText("");
      clearImage();
      return;
    }
    if (!trimmed) return;
    onSend(trimmed);
    setText("");
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      submit();
    }
  };

  /* A screenshot pasted with Ctrl+V is the fastest way to test a payment receipt. */
  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    if (!onSendImage || disabled) return;
    const file = Array.from(event.clipboardData.files)[0];
    if (file) {
      event.preventDefault();
      attach(file);
    }
  };

  const canSend = Boolean(text.trim()) || Boolean(image);
  const acceptsDrop = Boolean(onSendImage) && !disabled && !sending;

  const handleDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!acceptsDrop) return;
    event.preventDefault();
    setDragging(true);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    if (!acceptsDrop) return;
    event.preventDefault();
    setDragging(false);
    attach(event.dataTransfer.files[0]);
  };

  return (
    <div
      className="flex flex-col gap-2 border-t border-slate-200 bg-white p-3 sm:p-4"
      onDragOver={handleDragOver}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
    >
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

      {dragging && !image ? (
        <p className="rounded-md border border-dashed border-brand-300 bg-brand-50 px-3 py-2 text-sm text-brand-700">
          Отпустите изображение, чтобы прикрепить его
        </p>
      ) : null}

      {image ? (
        <div className="flex items-center gap-3 rounded-md border border-slate-200 bg-slate-50 p-2">
          {/* eslint-disable-next-line @next/next/no-img-element -- a local object URL, not a build-time asset */}
          <img src={image.preview} alt="" className="size-12 rounded object-cover" />
          <div className="min-w-0 flex-1 text-sm">
            <p className="truncate text-slate-700">{image.file.name}</p>
            <p className="text-xs text-slate-500">{Math.max(1, Math.round(image.file.size / 1024))} КБ</p>
          </div>
          <Button variant="ghost" size="sm" onClick={clearImage} disabled={sending}>
            Убрать
          </Button>
        </div>
      ) : null}

      <div className="flex items-end gap-2">
        {onSendImage ? (
          <>
            <input
              ref={fileInputRef}
              type="file"
              accept={IMAGE_ACCEPT}
              className="hidden"
              onChange={(event) => attach(event.target.files?.[0])}
            />
            <Button
              variant="outline"
              size="icon"
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled || sending}
              title="Прикрепить изображение (можно перетащить или вставить Ctrl+V)"
              aria-label="Прикрепить изображение"
            >
              📎
            </Button>
          </>
        ) : null}
        <Textarea
          aria-label="Ответ клиенту"
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          disabled={disabled || sending}
          rows={2}
          placeholder={
            disabled
              ? "Диалог ведёт бот"
              : image
                ? "Подпись к изображению (необязательно)"
                : "Введите сообщение… (Ctrl+Enter — отправить)"
          }
          containerClassName="flex-1"
          className="resize-none"
        />
        <Button onClick={submit} disabled={disabled || !canSend} loading={sending}>
          Отправить
        </Button>
      </div>
    </div>
  );
}
