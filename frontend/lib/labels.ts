/**
 * Russian labels and badge colours for every API enum (07-frontend.md §5).
 * Tailwind class strings are kept literal so the compiler can detect them.
 */

import type {
  ActorType,
  ConversationAwaiting,
  ConversationMode,
  CustomerType,
  CustomerTypeFilter,
  DateBasis,
  DeliveryStatus,
  DeliveryType,
  DispatchProvider,
  GeocodeStatus,
  GeoPrecision,
  Intent,
  IntegrationKey,
  Language,
  LocationSource,
  MessageDeliveryStatus,
  MessageDirection,
  MessageSender,
  MessageType,
  OrderEventType,
  OrderMissingField,
  OrderSource,
  OrderStatus,
  PaymentKind,
  PaymentMethod,
  PaymentStatus,
  StatisticsPeriod,
  UserRole,
} from "@/types/api";

/* ------------------------------------------------------------------ */
/* Badge tones                                                         */
/* ------------------------------------------------------------------ */

export type BadgeTone = "gray" | "blue" | "indigo" | "violet" | "amber" | "orange" | "green" | "teal" | "red";

export const BADGE_TONE_CLASSES: Record<BadgeTone, string> = {
  gray: "bg-slate-100 text-slate-700 ring-slate-500/20",
  blue: "bg-sky-50 text-sky-800 ring-sky-600/20",
  indigo: "bg-indigo-50 text-indigo-700 ring-indigo-600/20",
  violet: "bg-violet-50 text-violet-700 ring-violet-600/20",
  amber: "bg-amber-50 text-amber-800 ring-amber-600/30",
  orange: "bg-orange-50 text-orange-800 ring-orange-600/30",
  green: "bg-emerald-50 text-emerald-800 ring-emerald-600/20",
  teal: "bg-teal-50 text-teal-800 ring-teal-600/20",
  red: "bg-red-50 text-red-700 ring-red-600/20",
};

export function badgeToneClass(tone: BadgeTone | undefined): string {
  return BADGE_TONE_CLASSES[tone ?? "gray"];
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

export interface SelectOption<T extends string = string> {
  value: T;
  label: string;
}

/** Label with graceful fallback for unknown values (e.g. new backend enums). */
export function labelOf<T extends string>(
  labels: Record<T, string>,
  value: T | string | null | undefined,
  empty = "—",
): string {
  if (value === null || value === undefined || value === "") return empty;
  return (labels as Record<string, string>)[value] ?? value;
}

export function toneOf<T extends string>(tones: Record<T, BadgeTone>, value: T | string | null | undefined): BadgeTone {
  if (!value) return "gray";
  return (tones as Record<string, BadgeTone>)[value] ?? "gray";
}

/** Options for <Select> in declaration order of the labels map. */
export function optionsOf<T extends string>(labels: Record<T, string>): SelectOption<T>[] {
  return (Object.keys(labels) as T[]).map((value) => ({ value, label: labels[value] }));
}

/* ------------------------------------------------------------------ */
/* Users                                                               */
/* ------------------------------------------------------------------ */

export const USER_ROLE_LABELS: Record<UserRole, string> = {
  ADMIN: "Администратор",
  OPERATOR: "Оператор",
};

export const USER_ROLE_TONES: Record<UserRole, BadgeTone> = {
  ADMIN: "violet",
  OPERATOR: "blue",
};

export const LANGUAGE_LABELS: Record<Language, string> = {
  ru: "Русский",
  tg: "Таджикский",
};

export const LANGUAGE_TONES: Record<Language, BadgeTone> = {
  ru: "gray",
  tg: "teal",
};

/* ------------------------------------------------------------------ */
/* Customers                                                           */
/* ------------------------------------------------------------------ */

export const CUSTOMER_TYPE_LABELS: Record<CustomerType, string> = {
  NEW: "Новый клиент",
  REGULAR: "Постоянный клиент",
};

export const CUSTOMER_TYPE_TONES: Record<CustomerType, BadgeTone> = {
  NEW: "blue",
  REGULAR: "green",
};

export const CUSTOMER_TYPE_FILTER_LABELS: Record<CustomerTypeFilter, string> = {
  new: "Новые",
  regular: "Постоянные",
};

/* ------------------------------------------------------------------ */
/* Orders                                                              */
/* ------------------------------------------------------------------ */

export const ORDER_STATUS_LABELS: Record<OrderStatus, string> = {
  NEW: "Новый",
  WAITING_CONFIRMATION: "Ждёт подтверждения",
  CONFIRMED: "Подтверждён",
  PREPARING: "Готовится",
  READY: "Готов",
  HANDED_TO_COURIER: "Передан курьеру",
  COMPLETED: "Выполнен",
  CANCELLED: "Отменён",
};

export const ORDER_STATUS_TONES: Record<OrderStatus, BadgeTone> = {
  NEW: "gray",
  WAITING_CONFIRMATION: "amber",
  CONFIRMED: "blue",
  PREPARING: "indigo",
  READY: "violet",
  HANDED_TO_COURIER: "teal",
  COMPLETED: "green",
  CANCELLED: "red",
};

/** Button captions for status transitions (`allowed_transitions`). */
export const ORDER_STATUS_ACTION_LABELS: Record<OrderStatus, string> = {
  NEW: "Вернуть в черновик",
  WAITING_CONFIRMATION: "Отправить на подтверждение",
  CONFIRMED: "Подтвердить",
  PREPARING: "Начать готовить",
  READY: "Отметить готовым",
  HANDED_TO_COURIER: "Передать курьеру",
  COMPLETED: "Завершить",
  CANCELLED: "Отменить",
};

export const PAYMENT_STATUS_LABELS: Record<PaymentStatus, string> = {
  UNPAID: "Не оплачен",
  PARTIALLY_PAID: "Частично оплачен",
  PAID: "Оплачен",
  REFUNDED: "Возврат",
};

export const PAYMENT_STATUS_TONES: Record<PaymentStatus, BadgeTone> = {
  UNPAID: "red",
  PARTIALLY_PAID: "amber",
  PAID: "green",
  REFUNDED: "gray",
};

export const DELIVERY_TYPE_LABELS: Record<DeliveryType, string> = {
  DELIVERY: "Доставка",
  PICKUP: "Самовывоз",
};

export const DELIVERY_TYPE_TONES: Record<DeliveryType, BadgeTone> = {
  DELIVERY: "blue",
  PICKUP: "orange",
};

export const PAYMENT_METHOD_LABELS: Record<PaymentMethod, string> = {
  CASH: "Наличные",
  CARD: "Карта",
  TRANSFER: "Перевод",
  OTHER: "Другое",
};

export const PAYMENT_KIND_LABELS: Record<PaymentKind, string> = {
  PAYMENT: "Оплата",
  REFUND: "Возврат",
};

export const PAYMENT_KIND_TONES: Record<PaymentKind, BadgeTone> = {
  PAYMENT: "green",
  REFUND: "red",
};

export const ORDER_SOURCE_LABELS: Record<OrderSource, string> = {
  INSTAGRAM: "Instagram",
  ADMIN: "Админ-панель",
};

export const ORDER_SOURCE_TONES: Record<OrderSource, BadgeTone> = {
  INSTAGRAM: "violet",
  ADMIN: "gray",
};

export const ACTOR_TYPE_LABELS: Record<ActorType, string> = {
  USER: "Сотрудник",
  CUSTOMER: "Клиент",
  AI: "Бот (AI)",
  SYSTEM: "Система",
};

export const ACTOR_TYPE_TONES: Record<ActorType, BadgeTone> = {
  USER: "blue",
  CUSTOMER: "teal",
  AI: "violet",
  SYSTEM: "gray",
};

export const ORDER_EVENT_TYPE_LABELS: Record<OrderEventType, string> = {
  CREATED: "Заказ создан",
  UPDATED: "Заказ изменён",
  ITEMS_CHANGED: "Изменены позиции",
  STATUS_CHANGED: "Изменён статус",
  PAYMENT_CHANGED: "Изменена оплата",
  DELIVERY_CHANGED: "Изменена доставка",
  CONFIRMED: "Заказ подтверждён",
  CANCELLED: "Заказ отменён",
};

export const ORDER_EVENT_TYPE_TONES: Record<OrderEventType, BadgeTone> = {
  CREATED: "gray",
  UPDATED: "blue",
  ITEMS_CHANGED: "indigo",
  STATUS_CHANGED: "violet",
  PAYMENT_CHANGED: "green",
  DELIVERY_CHANGED: "teal",
  CONFIRMED: "blue",
  CANCELLED: "red",
};

export const ORDER_MISSING_FIELD_LABELS: Record<OrderMissingField, string> = {
  items: "Товары",
  delivery_date: "Дата",
  delivery_time: "Время",
  delivery_type: "Способ получения",
  customer_name: "Имя клиента",
  phone: "Телефон",
  address: "Адрес",
  recipient_name: "Получатель",
  location: "Координаты адреса",
};

/** Field names used in `OrderEventOut.changes`. */
export const ORDER_FIELD_LABELS: Record<string, string> = {
  status: "Статус",
  payment_status: "Оплата",
  payment_method: "Способ оплаты",
  paid_amount: "Оплачено",
  total_amount: "Сумма",
  delivery_type: "Тип получения",
  delivery_date: "Дата",
  delivery_time: "Время",
  delivery_address: "Адрес",
  delivery_latitude: "Широта",
  delivery_longitude: "Долгота",
  comment: "Комментарий",
  items: "Товары",
  customer_id: "Клиент",
  cancel_reason: "Причина отмены",
  recipient_name: "Получатель",
  recipient_phone: "Телефон получателя",
  courier_comment: "Комментарий курьеру",
  address_raw: "Адрес",
};

/* ------------------------------------------------------------------ */
/* Conversations                                                       */
/* ------------------------------------------------------------------ */

export const CONVERSATION_MODE_LABELS: Record<ConversationMode, string> = {
  AI: "Бот",
  HUMAN_HANDOFF: "У оператора",
};

export const CONVERSATION_MODE_TONES: Record<ConversationMode, BadgeTone> = {
  AI: "violet",
  HUMAN_HANDOFF: "amber",
};

export const CONVERSATION_AWAITING_LABELS: Record<ConversationAwaiting, string> = {
  missing_fields: "Уточнение данных заказа",
  confirmation: "Подтверждение заказа",
  cancel_confirmation: "Подтверждение отмены",
  address_choice: "Выбор адреса",
};

export const MESSAGE_DIRECTION_LABELS: Record<MessageDirection, string> = {
  INCOMING: "Входящее",
  OUTGOING: "Исходящее",
};

export const MESSAGE_TYPE_LABELS: Record<MessageType, string> = {
  TEXT: "Текст",
  VOICE: "Голосовое",
  IMAGE: "Изображение",
  SYSTEM: "Системное",
};

export const MESSAGE_TYPE_TONES: Record<MessageType, BadgeTone> = {
  TEXT: "gray",
  VOICE: "indigo",
  IMAGE: "teal",
  SYSTEM: "gray",
};

export const MESSAGE_SENDER_LABELS: Record<MessageSender, string> = {
  CUSTOMER: "Клиент",
  AI: "Бот",
  OPERATOR: "Оператор",
  SYSTEM: "Система",
};

export const MESSAGE_SENDER_TONES: Record<MessageSender, BadgeTone> = {
  CUSTOMER: "teal",
  AI: "violet",
  OPERATOR: "blue",
  SYSTEM: "gray",
};

export const MESSAGE_DELIVERY_STATUS_LABELS: Record<MessageDeliveryStatus, string> = {
  PENDING: "Отправляется",
  SENT: "Отправлено",
  FAILED: "Не отправлено",
  NOT_APPLICABLE: "—",
};

export const MESSAGE_DELIVERY_STATUS_TONES: Record<MessageDeliveryStatus, BadgeTone> = {
  PENDING: "amber",
  SENT: "green",
  FAILED: "red",
  NOT_APPLICABLE: "gray",
};

export const INTENT_LABELS: Record<Intent, string> = {
  FAQ: "Вопрос (FAQ)",
  PRODUCT_QUERY: "Вопрос о товаре",
  CREATE_ORDER: "Оформление заказа",
  CHANGE_ORDER: "Изменение заказа",
  CANCEL_ORDER: "Отмена заказа",
  DELIVERY_QUERY: "Вопрос о доставке",
  PAYMENT_QUERY: "Вопрос об оплате",
  ORDER_STATUS: "Статус заказа",
  GREETING: "Приветствие",
  COMPLAINT: "Жалоба",
  OPERATOR_REQUEST: "Запрос оператора",
  OTHER: "Другое",
};

export const INTENT_TONES: Record<Intent, BadgeTone> = {
  FAQ: "gray",
  PRODUCT_QUERY: "blue",
  CREATE_ORDER: "green",
  CHANGE_ORDER: "amber",
  CANCEL_ORDER: "red",
  DELIVERY_QUERY: "teal",
  PAYMENT_QUERY: "teal",
  ORDER_STATUS: "indigo",
  GREETING: "gray",
  COMPLAINT: "red",
  OPERATOR_REQUEST: "orange",
  OTHER: "gray",
};

/* ------------------------------------------------------------------ */
/* Deliveries                                                          */
/* ------------------------------------------------------------------ */

export const GEOCODE_STATUS_LABELS: Record<GeocodeStatus, string> = {
  PENDING: "Ожидает геокодирования",
  OK: "Адрес найден",
  NOT_FOUND: "Адрес не найден",
  AMBIGUOUS: "Неоднозначный адрес",
  FAILED: "Ошибка геокодирования",
  MANUAL: "Точка задана вручную",
};

export const GEOCODE_STATUS_TONES: Record<GeocodeStatus, BadgeTone> = {
  PENDING: "gray",
  OK: "green",
  NOT_FOUND: "red",
  AMBIGUOUS: "amber",
  FAILED: "red",
  MANUAL: "teal",
};

export const LOCATION_SOURCE_LABELS: Record<LocationSource, string> = {
  CUSTOMER_PIN: "Точка от клиента",
  GEOCODER: "Геокодер",
  OPERATOR: "Оператор",
  COURIER: "Курьер",
};

export const DELIVERY_STATUS_LABELS: Record<DeliveryStatus, string> = {
  PENDING: "Ожидает",
  AWAITING_DISPATCH: "Ожидает передачи в Maxim",
  DISPATCHED: "Передана курьеру",
  DELIVERED: "Доставлена",
  FAILED: "Не доставлена",
  CANCELLED: "Отменена",
};

export const DELIVERY_STATUS_TONES: Record<DeliveryStatus, BadgeTone> = {
  PENDING: "gray",
  AWAITING_DISPATCH: "amber",
  DISPATCHED: "blue",
  DELIVERED: "green",
  FAILED: "red",
  CANCELLED: "red",
};

export const DISPATCH_PROVIDER_LABELS: Record<DispatchProvider, string> = {
  MAXIM_MANUAL: "Maxim (вручную)",
  MAXIM_API: "Maxim API",
};

export const GEO_PRECISION_LABELS: Record<GeoPrecision, string> = {
  house: "Дом",
  street: "Улица",
  district: "Район",
  city: "Город",
  other: "Другое",
};

/* ------------------------------------------------------------------ */
/* Statistics / settings                                               */
/* ------------------------------------------------------------------ */

export const STATISTICS_PERIOD_LABELS: Record<StatisticsPeriod, string> = {
  today: "Сегодня",
  yesterday: "Вчера",
  week: "Неделя",
  month: "Месяц",
  custom: "Произвольный",
};

export const DATE_BASIS_LABELS: Record<DateBasis, string> = {
  delivery: "По дате доставки",
  created: "По дате подтверждения",
};

export const INTEGRATION_LABELS: Record<IntegrationKey, string> = {
  instagram: "Instagram",
  llm: "AI (LLM)",
  stt: "Распознавание речи",
  tts: "Синтез речи",
  geocoder: "Геокодирование",
  routing: "Маршрутизация",
  maxim: "Maxim",
};

export const BOOLEAN_LABELS = {
  true: "Да",
  false: "Нет",
} as const;

export const ACTIVE_LABELS = {
  true: "Активен",
  false: "Выключен",
} as const;
