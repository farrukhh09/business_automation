/**
 * Formatting helpers (07-frontend.md §1): money `12 500 сомони`, dates `ДД.ММ.ГГГГ`,
 * time `ЧЧ:ММ`, datetimes shown in the business timezone (Asia/Dushanbe).
 */

export const BUSINESS_TIMEZONE = "Asia/Dushanbe";
export const CURRENCY_LABEL = "сомони";
const EMPTY = "—";

type Numeric = number | string | null | undefined;

function toNumber(value: Numeric): number | null {
  if (value === null || value === undefined || value === "") return null;
  const num = typeof value === "number" ? value : Number(String(value).replace(",", "."));
  return Number.isFinite(num) ? num : null;
}

function groupThousands(integerDigits: string): string {
  return integerDigits.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

/**
 * `12500` → `"12 500"`, `12500.5` → `"12 500,50"` (kopecks only when non-zero).
 */
export function formatAmount(value: Numeric, empty = EMPTY): string {
  const num = toNumber(value);
  if (num === null) return empty;
  const rounded = Math.round(Math.abs(num) * 100);
  const integer = Math.floor(rounded / 100);
  const fraction = rounded % 100;
  const sign = num < 0 && rounded !== 0 ? "-" : "";
  const integerPart = groupThousands(String(integer));
  return fraction === 0 ? `${sign}${integerPart}` : `${sign}${integerPart},${String(fraction).padStart(2, "0")}`;
}

/** `12500` → `"12 500 сомони"`; `12500.5` → `"12 500,50 сомони"`. */
export function formatMoney(value: Numeric, empty = EMPTY): string {
  const amount = formatAmount(value, "");
  return amount ? `${amount} ${CURRENCY_LABEL}` : empty;
}

/** Integer/decimal with thousands separators: `1234.5` → `"1 234,5"`. */
export function formatNumber(value: Numeric, maxFractionDigits = 2, empty = EMPTY): string {
  const num = toNumber(value);
  if (num === null) return empty;
  const factor = 10 ** maxFractionDigits;
  const rounded = Math.round(Math.abs(num) * factor) / factor;
  const [intPart, fracPart] = String(rounded).split(".");
  const sign = num < 0 && rounded !== 0 ? "-" : "";
  return `${sign}${groupThousands(intPart)}${fracPart ? `,${fracPart}` : ""}`;
}

/** `2` + `"шт."` → `"2 шт."` */
export function formatQuantity(quantity: Numeric, unit?: string | null): string {
  const formatted = formatNumber(quantity, 3);
  return unit ? `${formatted} ${unit}` : formatted;
}

/* ------------------------------------------------------------------ */
/* Dates                                                               */
/* ------------------------------------------------------------------ */

const DATE_ONLY_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
const TIME_ONLY_RE = /^(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?$/;

interface ZonedParts {
  year: string;
  month: string;
  day: string;
  hour: string;
  minute: string;
  second: string;
}

const zonedFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: BUSINESS_TIMEZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function toDate(value: string | number | Date): Date | null {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Wall-clock parts of an instant in Asia/Dushanbe. */
export function getZonedParts(value: string | number | Date): ZonedParts | null {
  const date = toDate(value);
  if (!date) return null;
  const parts: Partial<ZonedParts> = {};
  for (const part of zonedFormatter.formatToParts(date)) {
    if (part.type in { year: 1, month: 1, day: 1, hour: 1, minute: 1, second: 1 }) {
      parts[part.type as keyof ZonedParts] = part.value;
    }
  }
  if (!parts.year || !parts.month || !parts.day) return null;
  return {
    year: parts.year,
    month: parts.month,
    day: parts.day,
    hour: parts.hour === "24" ? "00" : (parts.hour ?? "00"),
    minute: parts.minute ?? "00",
    second: parts.second ?? "00",
  };
}

/**
 * `"2026-09-16"` → `"16.09.2026"` (no timezone shift for plain dates);
 * ISO datetime → date in Asia/Dushanbe.
 */
export function formatDate(value: string | Date | null | undefined, empty = EMPTY): string {
  if (!value) return empty;
  if (typeof value === "string") {
    const match = DATE_ONLY_RE.exec(value);
    if (match) return `${match[3]}.${match[2]}.${match[1]}`;
  }
  const parts = getZonedParts(value);
  return parts ? `${parts.day}.${parts.month}.${parts.year}` : empty;
}

/**
 * `"18:00:00"` → `"18:00"`; ISO datetime → time in Asia/Dushanbe.
 */
export function formatTime(value: string | Date | null | undefined, empty = EMPTY): string {
  if (!value) return empty;
  if (typeof value === "string") {
    const match = TIME_ONLY_RE.exec(value);
    if (match) return `${match[1].padStart(2, "0")}:${match[2]}`;
  }
  const parts = getZonedParts(value);
  return parts ? `${parts.hour}:${parts.minute}` : empty;
}

/** ISO datetime → `"16.09.2026 18:00"` in Asia/Dushanbe. */
export function formatDateTime(value: string | Date | null | undefined, empty = EMPTY): string {
  if (!value) return empty;
  const parts = getZonedParts(value);
  return parts ? `${parts.day}.${parts.month}.${parts.year} ${parts.hour}:${parts.minute}` : empty;
}

/** Business date + time → `"16.09.2026 18:00"` (either part may be missing). */
export function formatDateAndTime(date: string | null | undefined, time: string | null | undefined, empty = EMPTY): string {
  const d = date ? formatDate(date, "") : "";
  const t = time ? formatTime(time, "") : "";
  const joined = [d, t].filter(Boolean).join(" ");
  return joined || empty;
}

const MONTHS_GENITIVE = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];

/** `"2026-09-16"` → `"16 сентября 2026"` (`withYear=false` → `"16 сентября"`). */
export function formatDateLong(value: string | null | undefined, withYear = true, empty = EMPTY): string {
  if (!value) return empty;
  const match = DATE_ONLY_RE.exec(value);
  let year: string;
  let month: string;
  let day: string;
  if (match) {
    [, year, month, day] = match;
  } else {
    const parts = getZonedParts(value);
    if (!parts) return empty;
    ({ year, month, day } = parts);
  }
  const text = `${Number(day)} ${MONTHS_GENITIVE[Number(month) - 1] ?? ""}`;
  return withYear ? `${text} ${year}` : text;
}

/** Today's business date `"YYYY-MM-DD"` (Asia/Dushanbe). */
export function businessToday(now: Date = new Date()): string {
  const parts = getZonedParts(now);
  if (!parts) return now.toISOString().slice(0, 10);
  return `${parts.year}-${parts.month}-${parts.day}`;
}

/** `addDays("2026-09-15", 1)` → `"2026-09-16"` (calendar arithmetic, no timezone). */
export function addDays(isoDate: string, days: number): string {
  const match = DATE_ONLY_RE.exec(isoDate);
  if (!match) return isoDate;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

/** Normalises `"HH:MM:SS"` → `"HH:MM"` for `<input type="time">`. */
export function toTimeInputValue(value: string | null | undefined): string {
  if (!value) return "";
  const match = TIME_ONLY_RE.exec(value);
  return match ? `${match[1].padStart(2, "0")}:${match[2]}` : "";
}

/* ------------------------------------------------------------------ */
/* Distances / durations                                               */
/* ------------------------------------------------------------------ */

/** `850` → `"850 м"`, `12345` → `"12,3 км"` */
export function formatDistance(meters: Numeric, empty = EMPTY): string {
  const m = toNumber(meters);
  if (m === null) return empty;
  if (Math.abs(m) < 1000) return `${Math.round(m)} м`;
  return `${formatNumber(m / 1000, 1)} км`;
}

/** `3900` → `"1 ч 05 мин"`, `300` → `"5 мин"` */
export function formatDuration(seconds: Numeric, empty = EMPTY): string {
  const s = toNumber(seconds);
  if (s === null) return empty;
  const totalMinutes = Math.round(Math.max(0, s) / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours === 0) return `${minutes} мин`;
  return `${hours} ч ${String(minutes).padStart(2, "0")} мин`;
}

/* ------------------------------------------------------------------ */
/* Misc                                                                */
/* ------------------------------------------------------------------ */

/** Customer display name: name → @username → phone → `Клиент #id`. */
export function customerDisplayName(customer: {
  id?: number;
  name?: string | null;
  username?: string | null;
  phone?: string | null;
}): string {
  if (customer.name) return customer.name;
  if (customer.username) return `@${customer.username}`;
  if (customer.phone) return customer.phone;
  return customer.id !== undefined ? `Клиент #${customer.id}` : "Клиент";
}

/** `"12"` → `"№12"` */
export function formatOrderNumber(id: number | string): string {
  return `№${id}`;
}

/** Coordinates → `"38.559800, 68.787000"` */
export function formatCoordinates(lat: Numeric, lng: Numeric, empty = EMPTY): string {
  const la = toNumber(lat);
  const ln = toNumber(lng);
  if (la === null || ln === null) return empty;
  return `${la.toFixed(6)}, ${ln.toFixed(6)}`;
}
