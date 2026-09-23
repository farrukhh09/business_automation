"use client";

import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { Button, useToast } from "@/components/ui";
import { mediaUrl, settingsApi } from "@/services/api";
import type { BusinessSettings } from "@/types/api";

/** Instagram accepts a picture sent by URL only as JPEG or PNG, up to 8 МБ (06 §1). */
const ACCEPT = "image/jpeg,image/png";
const MAX_BYTES = 8 * 1024 * 1024;

export interface PriceListImageFieldProps {
  /** Media file name from the settings, or `null` when no picture is uploaded. */
  filename: string | null;
  disabled?: boolean;
}

/**
 * The photo of the price list the bot sends when a customer asks about prices or the assortment
 * (03 §1.4). It is uploaded on its own, not with the rest of the form: the file goes straight to
 * `POST /settings/price-list-image`, and the answer is the settings as they now are.
 */
export function PriceListImageField({ filename, disabled = false }: PriceListImageFieldProps) {
  const toast = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  // The picture is kept here, not written into the settings query: replacing that answer would
  // re-initialise the whole settings form and throw away edits the admin has not saved yet.
  const [current, setCurrent] = useState<string | null>(filename);
  const [syncedFrom, setSyncedFrom] = useState<string | null>(filename);
  if (filename !== syncedFrom) {
    setSyncedFrom(filename);
    setCurrent(filename);
  }

  const saved = (settings: BusinessSettings) => {
    setCurrent(settings.price_list_image);
    setError(null);
  };

  const upload = useMutation({
    mutationFn: (file: File) => settingsApi.uploadPriceListImage(file),
    onSuccess: (settings) => {
      saved(settings);
      toast.success("Фото прайс-листа сохранено");
    },
    onError: (err) => toast.apiError(err, "Не удалось загрузить фото"),
  });

  const remove = useMutation({
    mutationFn: () => settingsApi.deletePriceListImage(),
    onSuccess: (settings) => {
      saved(settings);
      toast.success("Фото прайс-листа убрано");
    },
    onError: (err) => toast.apiError(err, "Не удалось убрать фото"),
  });

  const busy = disabled || upload.isPending || remove.isPending;

  const handleFile = (file: File | null) => {
    if (!file) return;
    if (file.size > MAX_BYTES) {
      setError("Изображение слишком большое: максимум 8 МБ");
      return;
    }
    if (!ACCEPT.split(",").includes(file.type)) {
      setError("Instagram принимает только JPEG или PNG");
      return;
    }
    setError(null);
    upload.mutate(file);
  };

  return (
    <div className="flex flex-col gap-3">
      <span className="text-sm font-medium text-slate-700">Фото прайс-листа</span>
      <p className="text-sm text-slate-500">
        Бот отправит это фото, когда клиент спросит про цены или ассортимент, и подпишет его одной строкой. Цены в
        заказе всё равно считаются по каталогу, поэтому после смены цен загрузите новое фото и обновите каталог.
      </p>
      {current ? (
        // eslint-disable-next-line @next/next/no-img-element -- media files are served by the backend, not by Next
        <img
          src={mediaUrl(current)}
          alt="Фото прайс-листа"
          className="max-h-80 w-auto self-start rounded-lg border border-slate-200 object-contain"
        />
      ) : (
        <p className="text-sm text-slate-600">
          Фото не загружено — на вопрос о ценах бот отвечает прайс-листом текстом.
        </p>
      )}
      {error ? <p className="text-sm text-rose-600">{error}</p> : null}
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={(event) => {
          handleFile(event.target.files?.[0] ?? null);
          event.target.value = "";
        }}
      />
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => inputRef.current?.click()} disabled={busy} loading={upload.isPending}>
          {current ? "Заменить фото" : "Загрузить фото"}
        </Button>
        {current ? (
          <Button variant="outline" onClick={() => remove.mutate()} disabled={busy} loading={remove.isPending}>
            Убрать фото
          </Button>
        ) : null}
      </div>
    </div>
  );
}
