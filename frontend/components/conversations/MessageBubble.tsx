import clsx from "clsx";

import { Badge } from "@/components/ui/Badge";
import { formatDateTime } from "@/lib/format";
import {
  INTENT_LABELS,
  INTENT_TONES,
  MESSAGE_DELIVERY_STATUS_LABELS,
  MESSAGE_DELIVERY_STATUS_TONES,
  MESSAGE_SENDER_LABELS,
  labelOf,
} from "@/lib/labels";
import { mediaUrl } from "@/services/api";
import type { Intent, MessageOut } from "@/types/api";

export interface MessageBubbleProps {
  message: MessageOut;
}

const SENDER_BUBBLE_CLASSES: Record<MessageOut["sender"], string> = {
  CUSTOMER: "bg-white border border-slate-200 text-slate-900",
  AI: "bg-violet-50 border border-violet-200 text-violet-950",
  OPERATOR: "bg-brand-600 border border-brand-600 text-white",
  SYSTEM: "bg-slate-100 border border-slate-200 text-slate-600",
};

function MessageContent({ message }: { message: MessageOut }) {
  const mutedClass = message.sender === "OPERATOR" ? "text-white/70" : "text-slate-500";

  if (message.message_type === "VOICE") {
    return (
      <div className="flex flex-col gap-1.5">
        {message.audio_url ? (
          <audio controls preload="none" src={mediaUrl(message.audio_url)} className="h-9 max-w-full" />
        ) : (
          <p className={clsx("text-sm italic", mutedClass)}>Аудиофайл недоступен</p>
        )}
        <p className={clsx("text-sm", message.text ? undefined : clsx("italic", mutedClass))}>
          {message.text ?? "не распознано"}
        </p>
      </div>
    );
  }

  if (message.message_type === "IMAGE") {
    if (!message.media_url) {
      return <p className={clsx("text-sm italic", mutedClass)}>Изображение недоступно</p>;
    }
    return (
      <a href={mediaUrl(message.media_url)} target="_blank" rel="noreferrer" className="block">
        {/* eslint-disable-next-line @next/next/no-img-element -- external/proxied media, not a build-time asset */}
        <img
          src={mediaUrl(message.media_url)}
          alt="Вложение"
          className="max-h-56 w-full max-w-64 rounded-lg object-cover"
          loading="lazy"
        />
      </a>
    );
  }

  return <p className="text-sm whitespace-pre-wrap break-words">{message.text ?? "—"}</p>;
}

/** One chat bubble, styled by direction (left/right) and sender. System messages are centered. */
export function MessageBubble({ message }: MessageBubbleProps) {
  if (message.sender === "SYSTEM") {
    return (
      <div className="flex justify-center py-1">
        <span className="rounded-full bg-slate-100 px-3 py-1 text-center text-xs text-slate-500">
          {message.text ?? "Системное сообщение"} · {formatDateTime(message.created_at)}
        </span>
      </div>
    );
  }

  const isOutgoing = message.direction === "OUTGOING";

  return (
    <div className={clsx("flex flex-col gap-1", isOutgoing ? "items-end" : "items-start")}>
      <div
        className={clsx(
          "max-w-[85%] rounded-2xl px-3.5 py-2.5 shadow-xs sm:max-w-[70%]",
          isOutgoing ? "rounded-br-sm" : "rounded-bl-sm",
          SENDER_BUBBLE_CLASSES[message.sender],
        )}
      >
        <div className={clsx("mb-1 text-xs font-medium", message.sender === "OPERATOR" ? "text-white/80" : "text-slate-500")}>
          {MESSAGE_SENDER_LABELS[message.sender]}
        </div>
        <MessageContent message={message} />
      </div>
      <div className="flex flex-wrap items-center gap-1.5 px-1 text-xs text-slate-400">
        <span>{formatDateTime(message.created_at)}</span>
        {!isOutgoing && message.intent ? (
          <Badge tone={INTENT_TONES[message.intent as Intent] ?? "gray"} className="text-[11px]">
            {labelOf(INTENT_LABELS, message.intent)}
          </Badge>
        ) : null}
        {isOutgoing && message.delivery_status !== "NOT_APPLICABLE" ? (
          <span title={message.delivery_status === "FAILED" ? (message.error ?? undefined) : undefined}>
            <Badge tone={MESSAGE_DELIVERY_STATUS_TONES[message.delivery_status]} className="text-[11px]">
              {MESSAGE_DELIVERY_STATUS_LABELS[message.delivery_status]}
            </Badge>
          </span>
        ) : null}
      </div>
    </div>
  );
}
