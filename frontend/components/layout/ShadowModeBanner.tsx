"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { queryKeys } from "@/lib/query";
import { settingsApi } from "@/services/api";

/**
 * Test mode reminder on every page (06 §1a): while it is on, nothing the bot writes reaches the
 * customers, so a staff member must never read the bot's answers in "Диалоги" as sent ones.
 */
export function ShadowModeBanner() {
  const { data } = useQuery({ queryKey: queryKeys.settings.business, queryFn: () => settingsApi.get() });
  if (!data?.bot_shadow_mode) return null;
  return (
    <div role="status" className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900 sm:px-6 lg:px-8">
      <strong className="font-semibold">Тестовый режим.</strong> Бот готовит ответы только в админке — клиентам в
      Instagram ничего не отправляется, отвечает менеджер.{" "}
      <Link href="/settings" className="font-medium underline">
        Настройки
      </Link>
    </div>
  );
}
