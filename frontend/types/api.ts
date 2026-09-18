/**
 * TypeScript types for the backend REST API.
 * Source of truth: docs/architecture/04-api.md and 02-data-model.md (enums).
 * Field names are 1:1 with the backend schemas — do not rename.
 */

/* ------------------------------------------------------------------ */
/* Scalars                                                             */
/* ------------------------------------------------------------------ */

/** JSON number with 2 decimal places, e.g. `1500.0`. */
export type Money = number;
/** `"YYYY-MM-DD"` (business date, Asia/Dushanbe). */
export type ISODate = string;
/** `"HH:MM"` (the API also accepts `"HH:MM:SS"` on input). */
export type ISOTime = string;
/** ISO 8601 datetime with timezone (UTC). */
export type ISODateTime = string;

/* ------------------------------------------------------------------ */
/* Enums (02-data-model.md). Arrays are exported for iteration/options */
/* ------------------------------------------------------------------ */

export const USER_ROLES = ["ADMIN", "OPERATOR"] as const;
export type UserRole = (typeof USER_ROLES)[number];

export const LANGUAGES = ["ru", "tg"] as const;
export type Language = (typeof LANGUAGES)[number];

export const ORDER_STATUSES = [
  "NEW",
  "WAITING_CONFIRMATION",
  "CONFIRMED",
  "PREPARING",
  "READY",
  "HANDED_TO_COURIER",
  "COMPLETED",
  "CANCELLED",
] as const;
export type OrderStatus = (typeof ORDER_STATUSES)[number];

export const PAYMENT_STATUSES = ["UNPAID", "PARTIALLY_PAID", "PAID", "REFUNDED"] as const;
export type PaymentStatus = (typeof PAYMENT_STATUSES)[number];

export const DELIVERY_TYPES = ["DELIVERY", "PICKUP"] as const;
export type DeliveryType = (typeof DELIVERY_TYPES)[number];

export const PAYMENT_METHODS = ["CASH", "CARD", "TRANSFER", "OTHER"] as const;
export type PaymentMethod = (typeof PAYMENT_METHODS)[number];

export const PAYMENT_KINDS = ["PAYMENT", "REFUND"] as const;
export type PaymentKind = (typeof PAYMENT_KINDS)[number];

export const ORDER_SOURCES = ["INSTAGRAM", "ADMIN"] as const;
export type OrderSource = (typeof ORDER_SOURCES)[number];

export const ACTOR_TYPES = ["USER", "CUSTOMER", "AI", "SYSTEM"] as const;
export type ActorType = (typeof ACTOR_TYPES)[number];

export const CONVERSATION_MODES = ["AI", "HUMAN_HANDOFF"] as const;
export type ConversationMode = (typeof CONVERSATION_MODES)[number];

export const MESSAGE_DIRECTIONS = ["INCOMING", "OUTGOING"] as const;
export type MessageDirection = (typeof MESSAGE_DIRECTIONS)[number];

export const MESSAGE_TYPES = ["TEXT", "VOICE", "IMAGE", "SYSTEM"] as const;
export type MessageType = (typeof MESSAGE_TYPES)[number];

export const MESSAGE_SENDERS = ["CUSTOMER", "AI", "OPERATOR", "SYSTEM"] as const;
export type MessageSender = (typeof MESSAGE_SENDERS)[number];

export const MESSAGE_DELIVERY_STATUSES = ["PENDING", "SENT", "FAILED", "NOT_APPLICABLE"] as const;
export type MessageDeliveryStatus = (typeof MESSAGE_DELIVERY_STATUSES)[number];

export const INTENTS = [
  "FAQ",
  "PRODUCT_QUERY",
  "CREATE_ORDER",
  "CHANGE_ORDER",
  "CANCEL_ORDER",
  "DELIVERY_QUERY",
  "PAYMENT_QUERY",
  "ORDER_STATUS",
  "GREETING",
  "COMPLAINT",
  "OPERATOR_REQUEST",
  "OTHER",
] as const;
export type Intent = (typeof INTENTS)[number];

export const GEOCODE_STATUSES = ["PENDING", "OK", "NOT_FOUND", "AMBIGUOUS", "FAILED", "MANUAL"] as const;
export type GeocodeStatus = (typeof GEOCODE_STATUSES)[number];

export const LOCATION_SOURCES = ["CUSTOMER_PIN", "GEOCODER", "OPERATOR", "COURIER"] as const;
export type LocationSource = (typeof LOCATION_SOURCES)[number];

export const DELIVERY_STATUSES = [
  "PENDING",
  "AWAITING_DISPATCH",
  "DISPATCHED",
  "DELIVERED",
  "FAILED",
  "CANCELLED",
] as const;
export type DeliveryStatus = (typeof DELIVERY_STATUSES)[number];

export const DISPATCH_PROVIDERS = ["MAXIM_MANUAL", "MAXIM_API"] as const;
export type DispatchProvider = (typeof DISPATCH_PROVIDERS)[number];

export const EXPENSE_CATEGORIES = [
  "INGREDIENTS",
  "PACKAGING",
  "DELIVERY",
  "MARKETING",
  "RENT",
  "SALARY",
  "EQUIPMENT",
  "OTHER",
] as const;
export type ExpenseCategory = (typeof EXPENSE_CATEGORIES)[number];

/* Derived / query-level enums (04-api.md) */

/** `CustomerListItem.customer_type` value. */
export const CUSTOMER_TYPES = ["NEW", "REGULAR"] as const;
export type CustomerType = (typeof CUSTOMER_TYPES)[number];

/** `GET /customers?customer_type=` filter value (lower case). */
export const CUSTOMER_TYPE_FILTERS = ["new", "regular"] as const;
export type CustomerTypeFilter = (typeof CUSTOMER_TYPE_FILTERS)[number];

export const CUSTOMER_SORTS = [
  "last_order_at",
  "-last_order_at",
  "created_at",
  "-created_at",
  "total_spent",
  "-total_spent",
] as const;
export type CustomerSort = (typeof CUSTOMER_SORTS)[number];

export const ORDER_SORTS = ["-created_at", "delivery_date", "-delivery_date", "total_amount"] as const;
export type OrderSort = (typeof ORDER_SORTS)[number];

export const STATISTICS_PERIODS = ["today", "yesterday", "week", "month", "custom"] as const;
export type StatisticsPeriod = (typeof STATISTICS_PERIODS)[number];

export const DATE_BASES = ["delivery", "created"] as const;
export type DateBasis = (typeof DATE_BASES)[number];

/** `OrderEventOut.event_type` (02-data-model.md, order_events). */
export const ORDER_EVENT_TYPES = [
  "CREATED",
  "UPDATED",
  "ITEMS_CHANGED",
  "STATUS_CHANGED",
  "PAYMENT_CHANGED",
  "DELIVERY_CHANGED",
  "CONFIRMED",
  "CANCELLED",
] as const;
export type OrderEventType = (typeof ORDER_EVENT_TYPES)[number];

/** `ConversationDetail.state_summary.awaiting` (05-ai.md §4). */
export const CONVERSATION_AWAITING = [
  "missing_fields",
  "confirmation",
  "cancel_confirmation",
  "address_choice",
] as const;
export type ConversationAwaiting = (typeof CONVERSATION_AWAITING)[number];

/** `OrderDetail.missing_fields` items (03-business-rules.md §1.3). */
export const ORDER_MISSING_FIELDS = [
  "items",
  "delivery_date",
  "delivery_time",
  "delivery_type",
  "customer_name",
  "phone",
  "address",
  "recipient_name",
  "location",
] as const;
export type OrderMissingField = (typeof ORDER_MISSING_FIELDS)[number];

/** Geocoder candidate precision (06-integrations.md §2). */
export const GEO_PRECISIONS = ["house", "street", "district", "city", "other"] as const;
export type GeoPrecision = (typeof GEO_PRECISIONS)[number];

/* ------------------------------------------------------------------ */
/* Common                                                              */
/* ------------------------------------------------------------------ */

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface PaginationParams {
  /** ≥ 1, default 1 */
  page?: number;
  /** 1..100, default 20 */
  page_size?: number;
}

/** Standard FastAPI 422 validation error item. */
export interface ValidationErrorItem {
  loc: (string | number)[];
  msg: string;
  type: string;
}

/** Error body: `{"detail": "текст на русском", "code": "machine_code"}` (or FastAPI 422). */
export interface ApiErrorBody {
  detail?: string | ValidationErrorItem[];
  code?: string;
  /** `order_incomplete` errors may carry the list of missing fields. */
  missing?: string[];
}

export interface HealthOut {
  status: "ok";
}

/* ------------------------------------------------------------------ */
/* 1. Auth                                                             */
/* ------------------------------------------------------------------ */

export interface LoginRequest {
  username: string;
  password: string;
}

export interface RefreshRequest {
  refresh_token: string;
}

export interface LogoutRequest {
  refresh_token: string;
}

export interface UserOut {
  id: number;
  username: string;
  full_name: string | null;
  role: UserRole;
  is_active: boolean;
  last_login_at: ISODateTime | null;
  created_at: ISODateTime;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  /** Access token lifetime, seconds. */
  expires_in: number;
  user: UserOut;
}

/* ------------------------------------------------------------------ */
/* 2. Users (ADMIN)                                                    */
/* ------------------------------------------------------------------ */

export interface UserCreate {
  username: string;
  full_name?: string | null;
  /** ≥ 8 characters */
  password: string;
  role: UserRole;
}

export interface UserUpdate {
  full_name?: string | null;
  password?: string;
  role?: UserRole;
  is_active?: boolean;
}

/* ------------------------------------------------------------------ */
/* 3. Customers                                                        */
/* ------------------------------------------------------------------ */

export interface CustomerListParams extends PaginationParams {
  /** name / username / phone */
  search?: string;
  customer_type?: CustomerTypeFilter;
  /** default `-last_order_at` */
  sort?: CustomerSort;
}

export interface CustomerListItem {
  id: number;
  name: string | null;
  username: string | null;
  phone: string | null;
  instagram_user_id: string | null;
  language: Language;
  is_new: boolean;
  is_blocked: boolean;
  customer_type: CustomerType;
  /** valid orders only */
  orders_count: number;
  /** valid orders only */
  total_spent: Money;
  last_order_at: ISODateTime | null;
  created_at: ISODateTime;
}

export interface CustomerDetail extends CustomerListItem {
  notes: string | null;
  conversation_id: number | null;
  /** all orders, newest first */
  orders: OrderListItem[];
}

export interface CustomerCreate {
  name: string;
  phone?: string | null;
  username?: string | null;
  language?: Language;
  notes?: string | null;
}

export interface CustomerUpdate {
  name?: string;
  phone?: string | null;
  language?: Language;
  notes?: string | null;
  is_blocked?: boolean;
}

/* ------------------------------------------------------------------ */
/* 4. Products                                                         */
/* ------------------------------------------------------------------ */

export interface ProductListParams {
  /** default false */
  include_inactive?: boolean;
  search?: string;
}

export interface ProductCreate {
  name: string;
  description?: string | null;
  /** > 0 */
  price: Money;
  /** default "TJS" */
  currency?: string;
  /** default "шт." */
  unit?: string;
  aliases?: string[];
  /** default true */
  is_active?: boolean;
  /** default 0 */
  sort_order?: number;
}

export type ProductUpdate = Partial<ProductCreate>;

export interface ProductOut {
  id: number;
  name: string;
  description: string | null;
  price: Money;
  currency: string;
  unit: string;
  aliases: string[];
  is_active: boolean;
  sort_order: number;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

/* ------------------------------------------------------------------ */
/* 5. Orders                                                           */
/* ------------------------------------------------------------------ */

export interface OrderListParams extends PaginationParams {
  /** repeatable: `?status=NEW&status=CONFIRMED` */
  status?: OrderStatus[];
  payment_status?: PaymentStatus;
  delivery_type?: DeliveryType;
  customer_id?: number;
  delivery_date?: ISODate;
  /** by `delivery_date` */
  date_from?: ISODate;
  /** by `delivery_date` */
  date_to?: ISODate;
  /** order number / name / phone */
  search?: string;
  /** default `-created_at` */
  sort?: OrderSort;
}

export interface OrderItemIn {
  product_id: number;
  /** ≥ 1 */
  quantity: number;
  comment?: string | null;
}

/**
 * Structured delivery address. Coordinates from staff → `location_source=OPERATOR`,
 * `geocode_status=MANUAL`; address without coordinates → geocoding is started.
 */
export interface DeliveryIn {
  address_raw?: string | null;
  district?: string | null;
  microdistrict?: string | null;
  street?: string | null;
  house?: string | null;
  apartment?: string | null;
  entrance?: string | null;
  floor?: string | null;
  landmark?: string | null;
  recipient_name?: string | null;
  recipient_phone?: string | null;
  courier_comment?: string | null;
  latitude?: number | null;
  longitude?: number | null;
}

export interface OrderCreate {
  customer_id: number;
  /** ≥ 1 item; prices are not accepted */
  items: OrderItemIn[];
  delivery_type: DeliveryType;
  delivery_date: ISODate;
  delivery_time: ISOTime;
  comment?: string | null;
  payment_method?: PaymentMethod | null;
  delivery?: DeliveryIn | null;
  /** default true: CONFIRMED immediately if complete, otherwise NEW */
  confirm?: boolean;
}

/**
 * OPERATOR may change only `status`, `payment_status`, `paid_amount`, `payment_method`,
 * `comment` and `delivery`; other fields → 403. ADMIN — everything.
 */
export interface OrderUpdate {
  /** replaces the whole item set */
  items?: OrderItemIn[];
  delivery_type?: DeliveryType;
  delivery_date?: ISODate;
  delivery_time?: ISOTime;
  comment?: string | null;
  status?: OrderStatus;
  payment_status?: PaymentStatus;
  paid_amount?: Money;
  payment_method?: PaymentMethod | null;
  delivery?: DeliveryIn | null;
}

export interface OrderCustomerRef {
  id: number;
  name: string | null;
  username: string | null;
  phone: string | null;
}

export interface OrderListItem {
  id: number;
  customer: OrderCustomerRef;
  status: OrderStatus;
  payment_status: PaymentStatus;
  delivery_type: DeliveryType | null;
  delivery_date: ISODate | null;
  delivery_time: ISOTime | null;
  delivery_address: string | null;
  total_amount: Money;
  paid_amount: Money;
  /** e.g. "Медовик ×2, Чизкейк ×1" */
  items_summary: string;
  /** Σ quantity */
  items_count: number;
  comment: string | null;
  created_at: ISODateTime;
}

export interface OrderItemOut {
  id: number;
  product_id: number | null;
  product_name: string;
  quantity: number;
  unit_price: Money;
  total_price: Money;
  comment: string | null;
}

export interface PaymentOut {
  id: number;
  kind: PaymentKind;
  amount: Money;
  method: PaymentMethod | null;
  note: string | null;
  paid_at: ISODateTime;
  created_by_user_id: number | null;
}

export interface OrderDetail extends OrderListItem {
  items: OrderItemOut[];
  payment_method: PaymentMethod | null;
  delivery_latitude: number | null;
  delivery_longitude: number | null;
  source: OrderSource;
  conversation_id: number | null;
  is_repeat_customer: boolean;
  confirmed_at: ISODateTime | null;
  completed_at: ISODateTime | null;
  cancelled_at: ISODateTime | null;
  cancel_reason: string | null;
  updated_at: ISODateTime;
  delivery: DeliveryOut | null;
  payments: PaymentOut[];
  allowed_transitions: OrderStatus[];
  missing_fields: string[];
}

export interface OrderStatusChangeRequest {
  status: OrderStatus;
  comment?: string | null;
}

export interface PaymentCreate {
  kind: PaymentKind;
  amount: Money;
  method?: PaymentMethod | null;
  note?: string | null;
}

export interface OrderCancelRequest {
  reason?: string | null;
}

export interface OrderEventActorUser {
  id: number;
  username: string;
}

export interface OrderEventOut {
  id: number;
  actor_type: ActorType;
  actor_user: OrderEventActorUser | null;
  event_type: OrderEventType | (string & Record<never, never>);
  /** `{"field": [old, new]}` */
  changes: Record<string, unknown>;
  comment: string | null;
  created_at: ISODateTime;
}

/* ------------------------------------------------------------------ */
/* 6. Production                                                       */
/* ------------------------------------------------------------------ */

export interface ProductionParams {
  /** default: today (business timezone) */
  date?: ISODate;
}

export interface ProductionItem {
  /** null when grouped by snapshot `product_name` */
  product_id: number | null;
  product_name: string;
  quantity: number;
  unit: string;
}

export interface ProductionSummaryOut {
  date: ISODate;
  orders_count: number;
  items: ProductionItem[];
  text: string;
}

/* ------------------------------------------------------------------ */
/* 7. Statistics                                                       */
/* ------------------------------------------------------------------ */

export interface StatisticsParams {
  period?: StatisticsPeriod;
  /** for `custom` */
  date_from?: ISODate;
  /** for `custom` */
  date_to?: ISODate;
  /** default `delivery` */
  date_basis?: DateBasis;
}

export interface StatisticsPeriodInfo {
  name: StatisticsPeriod | (string & Record<never, never>);
  date_from: ISODate;
  date_to: ISODate;
  date_basis: DateBasis;
}

export interface FinanceStats {
  revenue: Money;
  orders_count: number;
  paid_orders_count: number;
  partially_paid_orders_count: number;
  unpaid_orders_count: number;
  paid_amount: Money;
  unpaid_amount: Money;
  average_check: Money;
}

export interface CustomerStats {
  new_customers: number;
  regular_customers: number;
  total_customers: number;
  new_customer_orders: number;
  regular_customer_orders: number;
  customers_registered: number;
}

export interface DeliveryStats {
  delivery_orders: number;
  pickup_orders: number;
}

export interface ExpenseCategoryTotal {
  category: ExpenseCategory;
  amount: Money;
}

/** Revenue of the period minus expenses dated in the period (03-business-rules.md §4). */
export interface ProfitAndLossStats {
  revenue: Money;
  expenses: Money;
  /** may be negative */
  profit: Money;
  /** profit / revenue × 100, one decimal; `null` without revenue */
  margin_percent: number | null;
  /** non-zero categories, largest first */
  expenses_by_category: ExpenseCategoryTotal[];
}

export interface StatisticsOut {
  period: StatisticsPeriodInfo;
  finance: FinanceStats;
  customers: CustomerStats;
  delivery: DeliveryStats;
  profit_and_loss: ProfitAndLossStats;
}

export interface DashboardOut {
  date: ISODate;
  orders_today: number;
  revenue_today: Money;
  unpaid_orders_count: number;
  new_customers_today: number;
  regular_customers_today: number;
  delivery_orders_today: number;
  pickup_orders_today: number;
  waiting_confirmation_count: number;
  conversations_needing_attention: number;
  /** 10 latest */
  recent_orders: OrderListItem[];
}

export interface TimeseriesParams {
  date_from?: ISODate;
  date_to?: ISODate;
  date_basis?: DateBasis;
}

export interface TimeseriesPoint {
  date: ISODate;
  revenue: Money;
  orders_count: number;
  paid_amount: Money;
  /** by expense_date, whatever the date basis */
  expenses: Money;
  profit: Money;
}

/* ------------------------------------------------------------------ */
/* 7a. Expenses                                                        */
/* ------------------------------------------------------------------ */

export interface ExpenseListParams extends PaginationParams {
  date_from?: ISODate;
  date_to?: ISODate;
  category?: ExpenseCategory;
}

export interface ExpenseCreate {
  /** not in the future */
  expense_date: ISODate;
  category: ExpenseCategory;
  /** > 0 */
  amount: Money;
  comment?: string | null;
}

/** Omitted → unchanged; `comment: null` clears it. */
export type ExpenseUpdate = Partial<ExpenseCreate>;

export interface ExpenseOut {
  id: number;
  expense_date: ISODate;
  category: ExpenseCategory;
  amount: Money;
  comment: string | null;
  created_by_user_id: number | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

/* ------------------------------------------------------------------ */
/* 8. Reports                                                          */
/* ------------------------------------------------------------------ */

export interface DailyReportParams {
  /** default: yesterday if now < daily_report_time, otherwise today */
  date?: ISODate;
}

export interface DailyReportGenerateRequest {
  date: ISODate;
}

export interface DailyReportHistoryParams {
  /** default 30 */
  limit?: number;
}

export interface DailyReportData {
  finance: FinanceStats;
  customers: CustomerStats;
  delivery: DeliveryStats;
  /** Production summary for the report date (same shape as `GET /production`). */
  production: ProductionSummaryOut;
}

export interface DailyReportOut {
  date: ISODate;
  generated_at: ISODateTime;
  text: string;
  data: DailyReportData;
}

/** `GET /reports/daily/history` items — `DailyReportOut` without `data`. */
export type DailyReportHistoryItem = Omit<DailyReportOut, "data">;

/* ------------------------------------------------------------------ */
/* 9. Deliveries                                                       */
/* ------------------------------------------------------------------ */

export interface DeliveryListParams {
  /** default: today */
  date?: ISODate;
  status?: DeliveryStatus;
  geocode_status?: GeocodeStatus;
}

export interface GeoCandidate {
  formatted: string;
  lat: number;
  lng: number;
  precision: GeoPrecision | (string & Record<never, never>);
}

export interface DeliveryOut {
  id: number;
  order_id: number;
  address_raw: string;
  address_formatted: string | null;
  city: string;
  district: string | null;
  microdistrict: string | null;
  street: string | null;
  house: string | null;
  apartment: string | null;
  entrance: string | null;
  floor: string | null;
  landmark: string | null;
  latitude: number | null;
  longitude: number | null;
  location_source: LocationSource | null;
  geocode_status: GeocodeStatus;
  geocode_provider: string | null;
  geocode_candidates: GeoCandidate[];
  recipient_name: string | null;
  recipient_phone: string | null;
  courier_comment: string | null;
  status: DeliveryStatus;
  dispatch_provider: DispatchProvider | null;
  external_id: string | null;
  external_status: string | null;
  courier_name: string | null;
  courier_phone: string | null;
  dispatched_at: ISODateTime | null;
  delivered_at: ISODateTime | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface DeliveryOrderCustomerRef {
  id: number;
  name: string | null;
  phone: string | null;
}

export interface DeliveryOrderRef {
  id: number;
  status: OrderStatus;
  payment_status: PaymentStatus;
  total_amount: Money;
  paid_amount: Money;
  delivery_date: ISODate | null;
  delivery_time: ISOTime | null;
  items_summary: string;
  customer: DeliveryOrderCustomerRef;
}

export interface DeliveryListItem extends DeliveryOut {
  order: DeliveryOrderRef;
}

export interface DeliveryUpdate extends DeliveryIn {
  status?: DeliveryStatus;
  external_id?: string | null;
  external_status?: string | null;
  courier_name?: string | null;
  courier_phone?: string | null;
}

export interface SelectCandidateRequest {
  /** index in `geocode_candidates` */
  index: number;
}

export interface LocationLinkOut {
  url: string;
  expires_at: ISODateTime;
}

export interface RouteOptimizeRequest {
  date: ISODate;
  /** "HH:MM" */
  start_time?: ISOTime | null;
}

export interface RouteDateParams {
  date?: ISODate;
}

export interface RouteStart {
  name: string;
  latitude: number;
  longitude: number;
}

export interface RouteStopOut {
  sequence: number;
  delivery_id: number;
  order_id: number;
  address: string;
  latitude: number;
  longitude: number;
  /** The point is the place the geocoder found (microdistrict, street, landmark), not the house itself. */
  approximate: boolean;
  approximate_place: string | null;
  eta: ISOTime | null;
  desired_time: ISOTime | null;
  lateness_min: number;
  distance_from_prev_m: number;
  duration_from_prev_s: number;
  recipient_name: string | null;
  phone: string | null;
  courier_comment: string | null;
  items_summary: string;
}

export interface RouteUnlocatedOut {
  delivery_id: number;
  order_id: number;
  address: string;
  reason: string;
}

export interface RoutePlanOut {
  id: number;
  delivery_date: ISODate;
  start: RouteStart;
  start_time: ISOTime;
  algorithm: string;
  /** `osrm` | `haversine` */
  distance_source: string;
  total_distance_m: number;
  total_duration_s: number;
  created_at: ISODateTime;
  stops: RouteStopOut[];
  unlocated: RouteUnlocatedOut[];
}

export interface DispatchResultOut {
  delivery: DeliveryOut;
  provider: DispatchProvider;
  requires_operator: boolean;
  instructions: string;
  copy_text: string;
}

/**
 * `GET /deliveries/dispatch-sheet` stop. The contract only says `stops: [...]`
 * (in route order); the route-stop fields are expected but not guaranteed.
 */
export interface DispatchSheetStop extends Partial<RouteStopOut> {
  [key: string]: unknown;
}

export interface DispatchSheetOut {
  text: string;
  stops: DispatchSheetStop[];
}

/* ------------------------------------------------------------------ */
/* 10. FAQ                                                             */
/* ------------------------------------------------------------------ */

export interface FaqListParams {
  include_inactive?: boolean;
}

export interface FaqCreate {
  question: string;
  answer: string;
  question_tg?: string | null;
  answer_tg?: string | null;
  keywords?: string[];
  is_active?: boolean;
  sort_order?: number;
}

export type FaqUpdate = Partial<FaqCreate>;

export interface FaqOut {
  id: number;
  question: string;
  answer: string;
  question_tg: string | null;
  answer_tg: string | null;
  keywords: string[];
  is_active: boolean;
  sort_order: number;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

/* ------------------------------------------------------------------ */
/* 11. Conversations                                                   */
/* ------------------------------------------------------------------ */

export interface ConversationListParams extends PaginationParams {
  mode?: ConversationMode;
  needs_attention?: boolean;
  search?: string;
}

export interface ConversationDetailParams {
  /** load messages older than this id */
  before_id?: number;
  /** default 50 */
  limit?: number;
}

export interface ConversationCustomerRef {
  id: number;
  name: string | null;
  username: string | null;
  phone: string | null;
}

export interface ConversationListItem {
  id: number;
  customer: ConversationCustomerRef;
  mode: ConversationMode;
  needs_attention: boolean;
  handoff_reason: string | null;
  last_message_at: ISODateTime | null;
  last_message_preview: string | null;
  active_order_id: number | null;
}

export interface MessageOut {
  id: number;
  direction: MessageDirection;
  message_type: MessageType;
  sender: MessageSender;
  /** for VOICE — STT result */
  text: string | null;
  audio_url: string | null;
  media_url: string | null;
  intent: Intent | (string & Record<never, never>) | null;
  delivery_status: MessageDeliveryStatus;
  error: string | null;
  sent_by_user_id: number | null;
  created_at: ISODateTime;
}

export interface ConversationStateSummary {
  draft_order_id: number | null;
  /** 04-api.md: `string | null`; known values from 05-ai.md §4. */
  awaiting: ConversationAwaiting | (string & Record<never, never>) | null;
  language: Language;
}

export interface ConversationDetail extends ConversationListItem {
  /** ascending by time */
  messages: MessageOut[];
  state_summary: ConversationStateSummary;
}

export interface SendMessageRequest {
  text: string;
}

export interface HandoffRequest {
  reason?: string | null;
}

/* ------------------------------------------------------------------ */
/* 11a. Test chat (dev only)                                           */
/* ------------------------------------------------------------------ */

export interface TestChatOut {
  conversation_id: number | null;
  mode: ConversationMode;
  needs_attention: boolean;
  /** ascending by time */
  messages: MessageOut[];
}

/* ------------------------------------------------------------------ */
/* 12. Settings                                                        */
/* ------------------------------------------------------------------ */

export interface WarehouseSettings {
  name: string;
  address: string;
  latitude: number | null;
  longitude: number | null;
}

export interface BusinessSettings {
  business_name: string;
  ai_enabled: boolean;
  voice_replies_enabled: boolean;
  warehouse: WarehouseSettings;
  pickup_address: string;
  working_hours: string;
  /** "HH:MM" or null — the bot takes delivery/pickup times only inside these hours; null = no limit. */
  order_hours_start: ISOTime | null;
  order_hours_end: ISOTime | null;
  min_lead_time_hours: number;
  max_days_ahead: number;
  delivery_time_window_minutes: number;
  /** "HH:MM" */
  route_start_time: ISOTime;
  service_time_minutes: number;
  average_speed_kmh: number;
  /** "HH:MM" */
  daily_report_time: ISOTime;
  payment_methods_text: string;
  delivery_info_text: string;
  /** Prepayment asked by the bot after the customer's "Да"; the receipt screenshot is read by the bot. */
  prepayment_enabled: boolean;
  /** 1..100 — share of the order total. */
  prepayment_percent: number;
  /** Wallet phone number (Dushanbe City / Alif / Эсхата). */
  prepayment_wallet: string;
  /** Apps the wallet is registered in, as shown to the customer. */
  prepayment_wallet_banks: string;
  /** Mark the order paid from a matching receipt without the operator (off by default). */
  prepayment_auto_confirm: boolean;
}

/** `PUT /settings` accepts a partial update. */
export type BusinessSettingsUpdate = Partial<Omit<BusinessSettings, "warehouse">> & {
  warehouse?: Partial<WarehouseSettings>;
};

export interface IntegrationStatus {
  configured: boolean;
  provider: string | null;
  details: string;
}

export const INTEGRATION_KEYS = ["instagram", "llm", "stt", "tts", "geocoder", "routing", "maxim"] as const;
export type IntegrationKey = (typeof INTEGRATION_KEYS)[number];

export type IntegrationsStatusOut = Record<IntegrationKey, IntegrationStatus>;

/* ------------------------------------------------------------------ */
/* 13. Public location page                                            */
/* ------------------------------------------------------------------ */

export interface LatLng {
  lat: number;
  lng: number;
}

export interface PublicLocationOut {
  business_name: string;
  address_raw: string | null;
  latitude?: number | null;
  longitude?: number | null;
  city_center: LatLng;
  /** Where the geocoder thinks the address is (street / microdistrict) — the map starts there. */
  suggested?: LatLng | null;
  expired: boolean;
  used: boolean;
}

export interface PublicLocationSubmit {
  latitude: number;
  longitude: number;
}

export interface PublicLocationSubmitOut {
  ok: true;
}
