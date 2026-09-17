/**
 * Shared domain building blocks for the admin panel pages.
 * Import from "@/components/shared" (MapView loads Leaflet lazily on the client only).
 */

export { ConfirmActionButton, type ConfirmActionButtonProps } from "@/components/shared/ConfirmActionButton";
export { CopyButton, type CopyButtonProps } from "@/components/shared/CopyButton";
export {
  CustomerLink,
  PhoneLink,
  type CustomerLinkProps,
  type CustomerRef,
  type PhoneLinkProps,
} from "@/components/shared/CustomerLink";
export {
  FilterBar,
  FilterBarItem,
  type FilterBarItemProps,
  type FilterBarProps,
} from "@/components/shared/FilterBar";
export { KeyValueList, type KeyValueItem, type KeyValueListProps } from "@/components/shared/KeyValueList";
export { MapView } from "@/components/shared/MapView";
export type { LatLngTuple, MapMarker, MapMarkerTone, MapViewProps } from "@/components/shared/map-types";
export { MoneyText, type MoneyTextProps, type MoneyTextTone } from "@/components/shared/MoneyText";
export { OrderTable, type OrderTableProps } from "@/components/shared/OrderTable";
export {
  DEFAULT_DATE_BASIS,
  DEFAULT_PERIOD,
  normalizePeriodValue,
  PeriodPicker,
  periodValueError,
  usePeriodParams,
  type PeriodParamsApi,
  type PeriodPickerProps,
  type PeriodValue,
} from "@/components/shared/PeriodPicker";
export { SectionCard, type SectionCardProps } from "@/components/shared/SectionCard";
export {
  SegmentedControl,
  type SegmentedControlProps,
  type SegmentedOption,
} from "@/components/shared/SegmentedControl";
export {
  PageSkeleton,
  PageSuspense,
  TableSkeleton,
  type PageSuspenseProps,
  type TableSkeletonProps,
} from "@/components/shared/Skeletons";
export {
  ConversationModeBadge,
  CustomerTypeBadge,
  DeliveryStatusBadge,
  DeliveryTypeBadge,
  GeocodeStatusBadge,
  OrderStatusBadge,
  PaymentStatusBadge,
  type EnumBadgeWrapperProps,
} from "@/components/shared/StatusBadges";
