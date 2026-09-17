"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { MessageBubble } from "@/components/conversations/MessageBubble";
import { MessageComposer } from "@/components/conversations/MessageComposer";
import { TakeoverDialog } from "@/components/conversations/TakeoverDialog";
import { ConversationModeBadge } from "@/components/shared/StatusBadges";
import { CustomerLink } from "@/components/shared/CustomerLink";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { ErrorState } from "@/components/ui/ErrorState";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageSpinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { formatOrderNumber } from "@/lib/format";
import { CONVERSATION_AWAITING_LABELS, LANGUAGE_LABELS, labelOf } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { conversationsApi } from "@/services/api";
import { isApiError } from "@/services/http";
import type { ConversationDetail, MessageOut } from "@/types/api";

export interface ConversationDetailViewProps {
  id: string;
}

const DETAIL_PARAMS = { limit: 50 };

export function ConversationDetailView({ id }: ConversationDetailViewProps) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const detailKey = queryKeys.conversations.detail(id, DETAIL_PARAMS);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: detailKey,
    queryFn: () => conversationsApi.get(id, DETAIL_PARAMS),
    refetchInterval: () => (typeof document !== "undefined" && document.visibilityState === "hidden" ? false : 5_000),
  });

  const setDetailCache = (next: ConversationDetail) => {
    queryClient.setQueryData(detailKey, next);
  };

  /*
   * Older messages loaded via "Загрузить ранее" (before_id), kept separate from the polled page.
   * The parent mounts this view with `key={id}`, so switching conversations remounts it fresh —
   * no manual reset needed here.
   */
  const [olderMessages, setOlderMessages] = useState<MessageOut[]>([]);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [hasMoreOlder, setHasMoreOlder] = useState(true);

  const messages = [...olderMessages, ...(data?.messages ?? [])];

  const loadOlder = async () => {
    if (messages.length === 0 || loadingOlder) return;
    setLoadingOlder(true);
    try {
      const oldestId = messages[0].id;
      const page = await conversationsApi.get(id, { before_id: oldestId, limit: 50 });
      if (page.messages.length === 0) setHasMoreOlder(false);
      else setOlderMessages((prev) => [...page.messages, ...prev]);
    } catch (err) {
      toast.apiError(err, "Не удалось загрузить более ранние сообщения");
    } finally {
      setLoadingOlder(false);
    }
  };

  /* Mark the conversation read once, when it was flagged on open. */
  const markedReadRef = useRef<number | null>(null);
  useEffect(() => {
    if (!data || !data.needs_attention || markedReadRef.current === data.id) return;
    markedReadRef.current = data.id;
    conversationsApi
      .markRead(data.id)
      .then(() => {
        setDetailCache({ ...data, needs_attention: false });
        queryClient.invalidateQueries({ queryKey: queryKeys.conversations.lists });
      })
      .catch(() => {
        /* best effort */
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  /* Autoscroll: stay pinned to the bottom unless the user scrolled up. */
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedToBottomRef = useRef(true);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !pinnedToBottomRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  /* Take over / resume. */
  const [takeoverOpen, setTakeoverOpen] = useState(false);
  const [resumeOpen, setResumeOpen] = useState(false);

  const takeoverMutation = useMutation({
    mutationFn: (reason: string) => conversationsApi.handoff(id, { reason: reason || undefined }),
    onSuccess: (detail) => {
      setDetailCache(detail);
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.lists });
      toast.success("Диалог взят на себя");
      setTakeoverOpen(false);
    },
    onError: (err) => toast.apiError(err, "Не удалось взять диалог на себя"),
  });

  const resumeMutation = useMutation({
    mutationFn: () => conversationsApi.resume(id),
    onSuccess: (detail) => {
      setDetailCache(detail);
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.lists });
      toast.success("Диалог возвращён боту");
      setResumeOpen(false);
    },
    onError: (err) => toast.apiError(err, "Не удалось вернуть диалог боту"),
  });

  /* Sending a reply. */
  const [windowClosedError, setWindowClosedError] = useState<string | null>(null);

  const sendMutation = useMutation({
    mutationFn: (text: string) => conversationsApi.sendMessage(id, { text }),
    onSuccess: (message) => {
      setWindowClosedError(null);
      if (data) setDetailCache({ ...data, messages: [...data.messages, message], last_message_preview: message.text ?? data.last_message_preview, last_message_at: message.created_at });
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.lists });
    },
    onError: (err) => {
      if (isApiError(err) && err.code === "messaging_window_closed") {
        setWindowClosedError(
          "Не удалось отправить: прошло более 24 часов с последнего сообщения клиента. Instagram разрешает отвечать только в течение 24 часов после последнего сообщения от клиента — дождитесь нового обращения или свяжитесь с клиентом другим способом.",
        );
        toast.error("Окно ответа Instagram закрыто (24 часа)");
      } else {
        toast.apiError(err, "Не удалось отправить сообщение");
      }
    },
  });

  if (isLoading && !data) return <PageSpinner label="Загрузка диалога…" />;
  if (error && !data) return <ErrorState error={error} onRetry={refetch} />;
  if (!data) return null;

  const title = <>Диалог с <CustomerLink customer={data.customer} showUsername /></>;

  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[{ label: "Диалоги", href: "/conversations" }, { label: `#${id}` }]}
        actions={
          data.mode === "AI" ? (
            <Button onClick={() => setTakeoverOpen(true)}>Взять на себя</Button>
          ) : (
            <Button variant="outline" onClick={() => setResumeOpen(true)}>
              Вернуть боту
            </Button>
          )
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white p-3 text-sm sm:p-4">
        <ConversationModeBadge mode={data.mode} />
        <Badge tone="gray">{labelOf(LANGUAGE_LABELS, data.state_summary.language)}</Badge>
        {data.state_summary.awaiting ? (
          <Badge tone="amber">{labelOf(CONVERSATION_AWAITING_LABELS, data.state_summary.awaiting, data.state_summary.awaiting)}</Badge>
        ) : null}
        {data.state_summary.draft_order_id ? (
          <Link href={`/orders/${data.state_summary.draft_order_id}`} className="font-medium text-brand-700 hover:underline">
            Черновик заказа {formatOrderNumber(data.state_summary.draft_order_id)}
          </Link>
        ) : null}
        {data.active_order_id ? (
          <Link href={`/orders/${data.active_order_id}`} className="font-medium text-brand-700 hover:underline">
            Заказ {formatOrderNumber(data.active_order_id)}
          </Link>
        ) : null}
        {data.handoff_reason ? (
          <span className="text-slate-500">
            Причина обращения к оператору: <span className="text-slate-700">{data.handoff_reason}</span>
          </span>
        ) : null}
      </div>

      <div className="flex h-[65vh] min-h-96 flex-col overflow-hidden rounded-xl border border-slate-200 bg-slate-50 shadow-xs">
        <div ref={scrollRef} onScroll={handleScroll} className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3 sm:p-4">
          {hasMoreOlder ? (
            <div className="flex justify-center">
              <Button variant="outline" size="sm" onClick={loadOlder} loading={loadingOlder}>
                Загрузить ранее
              </Button>
            </div>
          ) : null}
          {messages.length === 0 ? (
            <p className="flex flex-1 items-center justify-center text-sm text-slate-400">Сообщений пока нет</p>
          ) : (
            messages.map((message) => <MessageBubble key={message.id} message={message} />)
          )}
        </div>

        <MessageComposer
          disabled={data.mode === "AI"}
          sending={sendMutation.isPending}
          onSend={(text) => sendMutation.mutate(text)}
          errorBanner={windowClosedError}
          onDismissError={() => setWindowClosedError(null)}
        />
      </div>

      {takeoverOpen ? (
        <TakeoverDialog
          loading={takeoverMutation.isPending}
          onConfirm={(reason) => takeoverMutation.mutate(reason)}
          onCancel={() => setTakeoverOpen(false)}
        />
      ) : null}

      <ConfirmDialog
        open={resumeOpen}
        title="Вернуть диалог боту?"
        description="Бот снова начнёт отвечать клиенту автоматически."
        confirmLabel="Вернуть боту"
        loading={resumeMutation.isPending}
        onConfirm={() => resumeMutation.mutate()}
        onCancel={() => setResumeOpen(false)}
      />
    </>
  );
}
