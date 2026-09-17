/**
 * Typed functions for every endpoint in docs/architecture/04-api.md.
 * Paths are relative to `/api` (see services/http.ts).
 */

import { http } from "@/services/http";
import type {
  BusinessSettings,
  BusinessSettingsUpdate,
  ConversationDetail,
  ConversationDetailParams,
  ConversationListItem,
  ConversationListParams,
  CustomerCreate,
  CustomerDetail,
  CustomerListItem,
  CustomerListParams,
  CustomerUpdate,
  DailyReportGenerateRequest,
  DailyReportHistoryItem,
  DailyReportHistoryParams,
  DailyReportOut,
  DailyReportParams,
  DashboardOut,
  DeliveryListItem,
  DeliveryListParams,
  DeliveryOut,
  DeliveryUpdate,
  DispatchResultOut,
  DispatchSheetOut,
  FaqCreate,
  FaqListParams,
  FaqOut,
  FaqUpdate,
  HandoffRequest,
  HealthOut,
  IntegrationsStatusOut,
  LocationLinkOut,
  LoginRequest,
  LogoutRequest,
  MessageOut,
  OrderCancelRequest,
  OrderCreate,
  OrderDetail,
  OrderEventOut,
  OrderListItem,
  OrderListParams,
  OrderStatusChangeRequest,
  OrderUpdate,
  Page,
  PaymentCreate,
  ProductCreate,
  ProductionParams,
  ProductionSummaryOut,
  ProductListParams,
  ProductOut,
  ProductUpdate,
  PublicLocationOut,
  PublicLocationSubmit,
  PublicLocationSubmitOut,
  RefreshRequest,
  RouteDateParams,
  RouteOptimizeRequest,
  RoutePlanOut,
  SelectCandidateRequest,
  SendMessageRequest,
  StatisticsOut,
  StatisticsParams,
  TimeseriesParams,
  TimeseriesPoint,
  TokenPair,
  UserCreate,
  UserOut,
  UserUpdate,
} from "@/types/api";

type Id = number | string;

const seg = (value: Id) => encodeURIComponent(String(value));

/* 1. Auth */
export const authApi = {
  /** POST /auth/login (PUBLIC) */
  login: (body: LoginRequest) => http.post<TokenPair>("/auth/login", body, { auth: false }),
  /** POST /auth/refresh (PUBLIC). Prefer `refreshSession()` from services/http — it de-duplicates and stores tokens. */
  refresh: (body: RefreshRequest) => http.post<TokenPair>("/auth/refresh", body, { auth: false }),
  /** POST /auth/logout → 204 */
  logout: (body: LogoutRequest) => http.post<void>("/auth/logout", body),
  /** GET /auth/me */
  me: () => http.get<UserOut>("/auth/me"),
};

/* 2. Users (ADMIN) */
export const usersApi = {
  list: () => http.get<UserOut[]>("/users"),
  create: (body: UserCreate) => http.post<UserOut>("/users", body),
  update: (id: Id, body: UserUpdate) => http.patch<UserOut>(`/users/${seg(id)}`, body),
  /** Deactivation → 204 */
  deactivate: (id: Id) => http.delete<void>(`/users/${seg(id)}`),
};

/* 3. Customers */
export const customersApi = {
  list: (params?: CustomerListParams) => http.get<Page<CustomerListItem>>("/customers", params),
  get: (id: Id) => http.get<CustomerDetail>(`/customers/${seg(id)}`),
  create: (body: CustomerCreate) => http.post<CustomerDetail>("/customers", body),
  update: (id: Id, body: CustomerUpdate) => http.patch<CustomerDetail>(`/customers/${seg(id)}`, body),
};

/* 4. Products */
export const productsApi = {
  list: (params?: ProductListParams) => http.get<ProductOut[]>("/products", params),
  get: (id: Id) => http.get<ProductOut>(`/products/${seg(id)}`),
  /** ADMIN */
  create: (body: ProductCreate) => http.post<ProductOut>("/products", body),
  /** ADMIN */
  update: (id: Id, body: ProductUpdate) => http.patch<ProductOut>(`/products/${seg(id)}`, body),
  /** ADMIN, soft delete → 204 */
  remove: (id: Id) => http.delete<void>(`/products/${seg(id)}`),
};

/* 5. Orders */
export const ordersApi = {
  list: (params?: OrderListParams) => http.get<Page<OrderListItem>>("/orders", params),
  /** ADMIN */
  create: (body: OrderCreate) => http.post<OrderDetail>("/orders", body),
  get: (id: Id) => http.get<OrderDetail>(`/orders/${seg(id)}`),
  /** STAFF (OPERATOR — restricted fields, see OrderUpdate) */
  update: (id: Id, body: OrderUpdate) => http.patch<OrderDetail>(`/orders/${seg(id)}`, body),
  changeStatus: (id: Id, body: OrderStatusChangeRequest) =>
    http.post<OrderDetail>(`/orders/${seg(id)}/status`, body),
  addPayment: (id: Id, body: PaymentCreate) => http.post<OrderDetail>(`/orders/${seg(id)}/payments`, body),
  cancel: (id: Id, body: OrderCancelRequest = {}) => http.post<OrderDetail>(`/orders/${seg(id)}/cancel`, body),
  events: (id: Id) => http.get<OrderEventOut[]>(`/orders/${seg(id)}/events`),
};

/* 6. Production */
export const productionApi = {
  /** GET /production?date=YYYY-MM-DD (default today) */
  get: (params?: ProductionParams) => http.get<ProductionSummaryOut>("/production", params),
};

/* 7. Statistics */
export const statisticsApi = {
  get: (params?: StatisticsParams) => http.get<StatisticsOut>("/statistics", params),
  dashboard: () => http.get<DashboardOut>("/statistics/dashboard"),
  timeseries: (params?: TimeseriesParams) => http.get<TimeseriesPoint[]>("/statistics/timeseries", params),
};

/* 8. Reports */
export const reportsApi = {
  daily: (params?: DailyReportParams) => http.get<DailyReportOut>("/reports/daily", params),
  /** ADMIN — rebuilds the report */
  generate: (body: DailyReportGenerateRequest) => http.post<DailyReportOut>("/reports/daily/generate", body),
  history: (params?: DailyReportHistoryParams) =>
    http.get<DailyReportHistoryItem[]>("/reports/daily/history", params),
};

/* 9. Deliveries */
export const deliveriesApi = {
  list: (params?: DeliveryListParams) => http.get<DeliveryListItem[]>("/deliveries", params),
  get: (id: Id) => http.get<DeliveryOut>(`/deliveries/${seg(id)}`),
  update: (id: Id, body: DeliveryUpdate) => http.patch<DeliveryOut>(`/deliveries/${seg(id)}`, body),
  /** Restart geocoding */
  geocode: (id: Id) => http.post<DeliveryOut>(`/deliveries/${seg(id)}/geocode`),
  selectCandidate: (id: Id, body: SelectCandidateRequest) =>
    http.post<DeliveryOut>(`/deliveries/${seg(id)}/select-candidate`, body),
  /** Link for the customer to pin the delivery point */
  createLocationLink: (id: Id) => http.post<LocationLinkOut>(`/deliveries/${seg(id)}/location-link`),
  optimize: (body: RouteOptimizeRequest) => http.post<RoutePlanOut>("/deliveries/optimize", body),
  /** Latest route plan for the date, or null */
  route: (params: RouteDateParams) => http.get<RoutePlanOut | null>("/deliveries/routes", params),
  dispatch: (id: Id) => http.post<DispatchResultOut>(`/deliveries/${seg(id)}/dispatch`),
  dispatchSheet: (params: RouteDateParams) => http.get<DispatchSheetOut>("/deliveries/dispatch-sheet", params),
};

/* 10. FAQ */
export const faqApi = {
  list: (params?: FaqListParams) => http.get<FaqOut[]>("/faq", params),
  /** ADMIN */
  create: (body: FaqCreate) => http.post<FaqOut>("/faq", body),
  /** ADMIN */
  update: (id: Id, body: FaqUpdate) => http.patch<FaqOut>(`/faq/${seg(id)}`, body),
  /** ADMIN, physical delete → 204 */
  remove: (id: Id) => http.delete<void>(`/faq/${seg(id)}`),
};

/* 11. Conversations */
export const conversationsApi = {
  list: (params?: ConversationListParams) => http.get<Page<ConversationListItem>>("/conversations", params),
  get: (id: Id, params?: ConversationDetailParams) =>
    http.get<ConversationDetail>(`/conversations/${seg(id)}`, params),
  /** Operator reply to Instagram (409 `messaging_window_closed` after 24 h) */
  sendMessage: (id: Id, body: SendMessageRequest) =>
    http.post<MessageOut>(`/conversations/${seg(id)}/messages`, body),
  handoff: (id: Id, body: HandoffRequest = {}) =>
    http.post<ConversationDetail>(`/conversations/${seg(id)}/handoff`, body),
  /** Return the conversation to the bot */
  resume: (id: Id) => http.post<ConversationDetail>(`/conversations/${seg(id)}/resume`),
  /** needs_attention=false → 204 */
  markRead: (id: Id) => http.post<void>(`/conversations/${seg(id)}/read`),
};

/* 12. Settings */
export const settingsApi = {
  get: () => http.get<BusinessSettings>("/settings"),
  /** ADMIN, partial update allowed */
  update: (body: BusinessSettingsUpdate) => http.put<BusinessSettings>("/settings", body),
  /** ADMIN */
  integrations: () => http.get<IntegrationsStatusOut>("/settings/integrations"),
};

/* 13. Public location page (no auth) */
export const publicLocationApi = {
  get: (token: string) => http.get<PublicLocationOut>(`/public/location/${seg(token)}`, undefined, { auth: false }),
  /** 410 when the link has expired */
  submit: (token: string, body: PublicLocationSubmit) =>
    http.post<PublicLocationSubmitOut>(`/public/location/${seg(token)}`, body, { auth: false }),
};

/* Service routes */
export const healthApi = {
  health: () => http.get<HealthOut>("/health", undefined, { auth: false }),
  ready: () => http.get<unknown>("/health/ready", undefined, { auth: false }),
};

/** URL of a TTS media file served by `GET /media/{filename}` (public). */
export function mediaUrl(filename: string): string {
  return `/api/media/${seg(filename)}`;
}
