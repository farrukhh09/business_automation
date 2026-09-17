import { EnumBadge, type EnumBadgeProps } from "@/components/ui/Badge";
import {
  CONVERSATION_MODE_LABELS,
  CONVERSATION_MODE_TONES,
  CUSTOMER_TYPE_LABELS,
  CUSTOMER_TYPE_TONES,
  DELIVERY_STATUS_LABELS,
  DELIVERY_STATUS_TONES,
  DELIVERY_TYPE_LABELS,
  DELIVERY_TYPE_TONES,
  GEOCODE_STATUS_LABELS,
  GEOCODE_STATUS_TONES,
  ORDER_STATUS_LABELS,
  ORDER_STATUS_TONES,
  PAYMENT_STATUS_LABELS,
  PAYMENT_STATUS_TONES,
} from "@/lib/labels";
import type {
  ConversationMode,
  CustomerType,
  DeliveryStatus,
  DeliveryType,
  GeocodeStatus,
  OrderStatus,
  PaymentStatus,
} from "@/types/api";

/** Badge props shared by the enum wrappers (everything except value/labels/tones). */
export type EnumBadgeWrapperProps = Omit<EnumBadgeProps<string>, "value" | "labels" | "tones">;

/** Order status with Russian label and colour; `null` → "—". Dot shown by default. */
export function OrderStatusBadge({
  status,
  dot = true,
  ...props
}: EnumBadgeWrapperProps & { status: OrderStatus | null | undefined }) {
  return <EnumBadge value={status} labels={ORDER_STATUS_LABELS} tones={ORDER_STATUS_TONES} dot={dot} {...props} />;
}

/** Payment status (Не оплачен / Частично оплачен / Оплачен / Возврат). */
export function PaymentStatusBadge({
  status,
  ...props
}: EnumBadgeWrapperProps & { status: PaymentStatus | null | undefined }) {
  return <EnumBadge value={status} labels={PAYMENT_STATUS_LABELS} tones={PAYMENT_STATUS_TONES} {...props} />;
}

/** Delivery type (Доставка / Самовывоз). */
export function DeliveryTypeBadge({
  type,
  ...props
}: EnumBadgeWrapperProps & { type: DeliveryType | null | undefined }) {
  return <EnumBadge value={type} labels={DELIVERY_TYPE_LABELS} tones={DELIVERY_TYPE_TONES} {...props} />;
}

/** Delivery (courier) status. */
export function DeliveryStatusBadge({
  status,
  ...props
}: EnumBadgeWrapperProps & { status: DeliveryStatus | null | undefined }) {
  return <EnumBadge value={status} labels={DELIVERY_STATUS_LABELS} tones={DELIVERY_STATUS_TONES} {...props} />;
}

/** Geocoding status of a delivery address. */
export function GeocodeStatusBadge({
  status,
  ...props
}: EnumBadgeWrapperProps & { status: GeocodeStatus | null | undefined }) {
  return <EnumBadge value={status} labels={GEOCODE_STATUS_LABELS} tones={GEOCODE_STATUS_TONES} {...props} />;
}

/** Новый / Постоянный клиент. */
export function CustomerTypeBadge({
  type,
  ...props
}: EnumBadgeWrapperProps & { type: CustomerType | null | undefined }) {
  return <EnumBadge value={type} labels={CUSTOMER_TYPE_LABELS} tones={CUSTOMER_TYPE_TONES} {...props} />;
}

/** Conversation mode (Бот / У оператора). */
export function ConversationModeBadge({
  mode,
  ...props
}: EnumBadgeWrapperProps & { mode: ConversationMode | null | undefined }) {
  return <EnumBadge value={mode} labels={CONVERSATION_MODE_LABELS} tones={CONVERSATION_MODE_TONES} {...props} />;
}
