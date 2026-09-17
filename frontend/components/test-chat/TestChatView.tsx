"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { MessageBubble } from "@/components/conversations/MessageBubble";
import { MessageComposer } from "@/components/conversations/MessageComposer";
import { ConversationModeBadge } from "@/components/shared/StatusBadges";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageSpinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { queryKeys } from "@/lib/query";
import { testChatApi } from "@/services/api";
import { isApiError } from "@/services/http";

const STORAGE_KEY = "bakery-admin.test-chat.customer-key";

function newCustomerKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().replace(/-/g, "")
    : `k${Date.now()}${Math.random().toString(36).slice(2)}`;
}

function readOrCreateCustomerKey(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const existing = window.localStorage.getItem(STORAGE_KEY);
    if (existing) return existing;
    const created = newCustomerKey();
    window.localStorage.setItem(STORAGE_KEY, created);
    return created;
  } catch {
    return newCustomerKey(); // storage unavailable (private mode) — works for this page load only
  }
}

/** "Тест бота" — talk to the bot from the browser, no Instagram, no real customer touched. */
export function TestChatView() {
  const queryClient = useQueryClient();
  const toast = useToast();

  const [customerKey, setCustomerKey] = useState<string | null>(readOrCreateCustomerKey);

  const detailKey = customerKey ? queryKeys.testChat.detail(customerKey) : queryKeys.testChat.all;
  const { data, isLoading, error } = useQuery({
    queryKey: detailKey,
    queryFn: () => testChatApi.get(customerKey as string),
    enabled: Boolean(customerKey),
  });

  const [sending, setSending] = useState(false);
  const send = async (text: string) => {
    if (!customerKey) return;
    setSending(true);
    try {
      const next = await testChatApi.sendMessage(customerKey, { text });
      queryClient.setQueryData(queryKeys.testChat.detail(customerKey), next);
    } catch (err) {
      toast.apiError(err, "Не удалось отправить сообщение");
    } finally {
      setSending(false);
    }
  };

  const [resetOpen, setResetOpen] = useState(false);
  const reset = () => {
    const key = newCustomerKey();
    window.localStorage.setItem(STORAGE_KEY, key);
    setCustomerKey(key);
    setResetOpen(false);
    toast.success("Начат новый тестовый диалог");
  };

  /* Autoscroll to the newest message. */
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [data?.messages.length]);

  if (isApiError(error) && error.status === 404) {
    return (
      <>
        <PageHeader title="Тест бота" />
        <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-600">
          Тестовый чат доступен только в режиме разработки (<code>APP_ENV=development</code>) —
          на этом сервере он выключен.
        </div>
      </>
    );
  }

  const messages = data?.messages ?? [];

  return (
    <>
      <PageHeader
        title="Тест бота"
        actions={
          <Button variant="outline" onClick={() => setResetOpen(true)}>
            Начать заново
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white p-3 text-sm sm:p-4">
        <span className="text-slate-500">
          Пишите сюда как клиент — сообщения проходят через настоящего бота, но никуда не
          отправляются.
        </span>
        {data ? <ConversationModeBadge mode={data.mode} /> : null}
        {data?.conversation_id ? (
          <Link
            href={`/conversations/${data.conversation_id}`}
            className="font-medium text-brand-700 hover:underline"
          >
            Открыть в «Диалоги» (#{data.conversation_id})
          </Link>
        ) : null}
      </div>

      <div className="flex h-[65vh] min-h-96 flex-col overflow-hidden rounded-xl border border-slate-200 bg-slate-50 shadow-xs">
        <div ref={scrollRef} className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3 sm:p-4">
          {isLoading ? (
            <PageSpinner label="Загрузка…" />
          ) : messages.length === 0 ? (
            <p className="flex flex-1 items-center justify-center text-sm text-slate-400">
              Напишите первое сообщение, чтобы начать
            </p>
          ) : (
            messages.map((message) => <MessageBubble key={message.id} message={message} />)
          )}
        </div>

        <MessageComposer disabled={!customerKey} sending={sending} onSend={send} />
      </div>

      <ConfirmDialog
        open={resetOpen}
        title="Начать новый тестовый диалог?"
        description="Текущая переписка останется в разделе «Диалоги», но здесь начнётся разговор с чистого листа, как от нового клиента."
        confirmLabel="Начать заново"
        onConfirm={reset}
        onCancel={() => setResetOpen(false)}
      />
    </>
  );
}
