"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { IntegrationsPanel } from "@/components/settings/IntegrationsPanel";
import { WarehouseMapField } from "@/components/settings/WarehouseMapField";
import { SectionCard } from "@/components/shared";
import { Button, ErrorState, Input, PageHeader, PageSpinner, Switch, TimeInput, useToast } from "@/components/ui";
import { Textarea } from "@/components/ui/Textarea";
import { useAuth } from "@/lib/auth";
import { queryKeys } from "@/lib/query";
import { settingsApi } from "@/services/api";
import { isApiError } from "@/services/http";
import type { BusinessSettings, BusinessSettingsUpdate } from "@/types/api";

/** Index = `date.weekday()` of the backend: 0 — понедельник … 6 — воскресенье. */
const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"] as const;

interface FormState {
  business_name: string;
  ai_enabled: boolean;
  voice_replies_enabled: boolean;
  warehouse_name: string;
  warehouse_address: string;
  warehouse_latitude: number | null;
  warehouse_longitude: number | null;
  pickup_address: string;
  working_hours: string;
  order_hours_start: string;
  order_hours_end: string;
  closed_weekdays: number[];
  min_order_quantity: string;
  order_quantity_step: string;
  min_lead_time_hours: string;
  max_days_ahead: string;
  follow_up_enabled: boolean;
  follow_up_after_hours: string;
  delivery_time_window_minutes: string;
  route_start_time: string;
  service_time_minutes: string;
  average_speed_kmh: string;
  daily_report_time: string;
  payment_methods_text: string;
  delivery_info_text: string;
  prepayment_enabled: boolean;
  prepayment_percent: string;
  prepayment_wallet: string;
  prepayment_wallet_banks: string;
  prepayment_auto_confirm: boolean;
}

function toFormState(settings: BusinessSettings): FormState {
  return {
    business_name: settings.business_name,
    ai_enabled: settings.ai_enabled,
    voice_replies_enabled: settings.voice_replies_enabled,
    warehouse_name: settings.warehouse.name,
    warehouse_address: settings.warehouse.address,
    warehouse_latitude: settings.warehouse.latitude,
    warehouse_longitude: settings.warehouse.longitude,
    pickup_address: settings.pickup_address,
    working_hours: settings.working_hours,
    order_hours_start: settings.order_hours_start ?? "",
    order_hours_end: settings.order_hours_end ?? "",
    closed_weekdays: [...settings.closed_weekdays],
    min_order_quantity: String(settings.min_order_quantity),
    order_quantity_step: String(settings.order_quantity_step),
    min_lead_time_hours: String(settings.min_lead_time_hours),
    max_days_ahead: String(settings.max_days_ahead),
    follow_up_enabled: settings.follow_up_enabled,
    follow_up_after_hours: String(settings.follow_up_after_hours),
    delivery_time_window_minutes: String(settings.delivery_time_window_minutes),
    route_start_time: settings.route_start_time,
    service_time_minutes: String(settings.service_time_minutes),
    average_speed_kmh: String(settings.average_speed_kmh),
    daily_report_time: settings.daily_report_time,
    payment_methods_text: settings.payment_methods_text,
    delivery_info_text: settings.delivery_info_text,
    prepayment_enabled: settings.prepayment_enabled,
    prepayment_percent: String(settings.prepayment_percent),
    prepayment_wallet: settings.prepayment_wallet,
    prepayment_wallet_banks: settings.prepayment_wallet_banks,
    prepayment_auto_confirm: settings.prepayment_auto_confirm,
  };
}

function toUpdateBody(form: FormState): BusinessSettingsUpdate {
  return {
    business_name: form.business_name.trim(),
    ai_enabled: form.ai_enabled,
    voice_replies_enabled: form.voice_replies_enabled,
    warehouse: {
      name: form.warehouse_name.trim(),
      address: form.warehouse_address.trim(),
      latitude: form.warehouse_latitude,
      longitude: form.warehouse_longitude,
    },
    pickup_address: form.pickup_address.trim(),
    working_hours: form.working_hours.trim(),
    // An empty field is sent as null: the limit is removed (04 §12).
    order_hours_start: form.order_hours_start || null,
    order_hours_end: form.order_hours_end || null,
    closed_weekdays: [...form.closed_weekdays].sort((left, right) => left - right),
    min_order_quantity: Number(form.min_order_quantity),
    order_quantity_step: Number(form.order_quantity_step),
    min_lead_time_hours: Number(form.min_lead_time_hours),
    max_days_ahead: Number(form.max_days_ahead),
    follow_up_enabled: form.follow_up_enabled,
    follow_up_after_hours: Number(form.follow_up_after_hours),
    delivery_time_window_minutes: Number(form.delivery_time_window_minutes),
    route_start_time: form.route_start_time,
    service_time_minutes: Number(form.service_time_minutes),
    average_speed_kmh: Number(form.average_speed_kmh),
    daily_report_time: form.daily_report_time,
    payment_methods_text: form.payment_methods_text,
    delivery_info_text: form.delivery_info_text,
    prepayment_enabled: form.prepayment_enabled,
    prepayment_percent: Number(form.prepayment_percent),
    prepayment_wallet: form.prepayment_wallet.trim(),
    prepayment_wallet_banks: form.prepayment_wallet_banks.trim(),
    prepayment_auto_confirm: form.prepayment_auto_confirm,
  };
}

export default function SettingsPage() {
  const { isAdmin } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: queryKeys.settings.business,
    queryFn: () => settingsApi.get(),
  });

  const [form, setForm] = useState<FormState | null>(null);
  const [original, setOriginal] = useState<FormState | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  // Tracks which server response the form was last initialised from, so it can be re-synced
  // after a save (fresh `data` reference) without ever clobbering in-progress unsaved edits.
  const [syncedFrom, setSyncedFrom] = useState<BusinessSettings | null>(null);

  if (data && data !== syncedFrom) {
    setSyncedFrom(data);
    const next = toFormState(data);
    setForm(next);
    setOriginal(next);
  }

  const dirty = form !== null && original !== null && JSON.stringify(form) !== JSON.stringify(original);

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  const mutation = useMutation({
    mutationFn: (body: BusinessSettingsUpdate) => settingsApi.update(body),
    onSuccess: (settings) => {
      queryClient.setQueryData(queryKeys.settings.business, settings);
      toast.success("Настройки сохранены");
    },
    onError: (err) => {
      if (isApiError(err) && Object.keys(err.fieldErrors).length > 0) setErrors(err.fieldErrors);
      toast.apiError(err, "Не удалось сохранить настройки");
    },
  });

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
  };

  const handleSave = () => {
    if (!form) return;
    const numericFields: Array<[keyof FormState, string]> = [
      ["min_lead_time_hours", "Срок предзаказа"],
      ["max_days_ahead", "Дней вперёд"],
      ["delivery_time_window_minutes", "Окно доставки"],
      ["service_time_minutes", "Время на точке"],
      ["average_speed_kmh", "Средняя скорость"],
    ];
    const nextErrors: Record<string, string> = {};
    for (const [key, label] of numericFields) {
      const value = Number(form[key]);
      if (!form[key] || Number.isNaN(value) || value < 0) nextErrors[key] = `${label}: введите число ≥ 0`;
    }
    for (const [key, label] of [
      ["min_order_quantity", "Минимальный заказ"],
      ["order_quantity_step", "Кратность заказа"],
    ] as Array<[keyof FormState, string]>) {
      const value = Number(form[key]);
      if (!form[key] || !Number.isInteger(value) || value < 1) nextErrors[key] = `${label}: целое число ≥ 1`;
    }
    const percent = Number(form.prepayment_percent);
    if (!form.prepayment_percent || Number.isNaN(percent) || percent < 1 || percent > 100) {
      nextErrors.prepayment_percent = "Доля предоплаты: число от 1 до 100";
    }
    // Больше суток напоминать нельзя: Instagram закрывает окно ответа через 24 часа (06 §1).
    const followUpHours = Number(form.follow_up_after_hours);
    if (!form.follow_up_after_hours || !Number.isInteger(followUpHours) || followUpHours < 1 || followUpHours > 23) {
      nextErrors.follow_up_after_hours = "Напоминание: целое число от 1 до 23 часов";
    }
    if (form.order_hours_start && form.order_hours_end && form.order_hours_end <= form.order_hours_start) {
      nextErrors.order_hours_end = "Окончание приёма заказов должно быть позже начала";
    }
    if (form.prepayment_enabled && !form.prepayment_wallet.trim()) {
      nextErrors.prepayment_wallet = "Укажите номер кошелька или карты, иначе бот не сможет попросить предоплату";
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) {
      toast.error("Проверьте правильность заполнения полей");
      return;
    }
    mutation.mutate(toUpdateBody(form));
  };

  const handleDiscard = () => {
    if (data) setForm(toFormState(data));
    setErrors({});
  };

  if (isLoading || !form) {
    return (
      <>
        <PageHeader title="Настройки" description="Параметры бизнеса, AI, доставки и статус интеграций" />
        {error ? <ErrorState error={error} onRetry={refetch} /> : <PageSpinner />}
      </>
    );
  }

  const disabled = !isAdmin || mutation.isPending;

  return (
    <>
      <PageHeader
        title="Настройки"
        description="Параметры бизнеса, AI, доставки и статус интеграций"
        actions={
          isAdmin ? (
            <>
              <Button variant="outline" onClick={handleDiscard} disabled={!dirty || mutation.isPending}>
                Отменить изменения
              </Button>
              <Button onClick={handleSave} loading={mutation.isPending} disabled={!dirty}>
                Сохранить
              </Button>
            </>
          ) : undefined
        }
      />

      {!isAdmin ? (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Настройки доступны только для просмотра. Изменять их может администратор.
        </div>
      ) : null}
      {isAdmin && dirty ? (
        <div className="mb-4 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-800">
          Есть несохранённые изменения.
        </div>
      ) : null}

      <div className="flex flex-col gap-6">
        <SectionCard title="Основное" description="Название бизнеса, которое видят клиенты">
          <Input
            label="Название бизнеса"
            value={form.business_name}
            onChange={(event) => update("business_name", event.target.value)}
            disabled={disabled}
          />
        </SectionCard>

        <SectionCard title="Бот" description="AI-ответы и голосовые сообщения в Instagram">
          <div className="flex flex-col gap-4">
            <Switch
              checked={form.ai_enabled}
              onCheckedChange={(value) => update("ai_enabled", value)}
              disabled={disabled}
              label="AI-ответы включены"
              description="При выключении бот перестаёт отвечать клиентам автоматически — все диалоги нужно вести вручную."
            />
            <Switch
              checked={form.voice_replies_enabled}
              onCheckedChange={(value) => update("voice_replies_enabled", value)}
              disabled={disabled}
              label="Голосовые ответы"
              description="Голосовые ответы недоступны на таджикском языке и отправляются только русскоязычным клиентам."
            />
            <Switch
              checked={form.follow_up_enabled}
              onCheckedChange={(value) => update("follow_up_enabled", value)}
              disabled={disabled}
              label="Догоняющие вопросы"
              description="Клиент замолчал на середине заказа — бот один раз сам напомнит о себе: «Вам коробочку оставить?», «Заказ оформляем?». Диалоги, которые ведёт менеджер, не трогает."
            />
            <Input
              label="Напоминать через, ч"
              type="number"
              min="1"
              max="23"
              step="1"
              value={form.follow_up_after_hours}
              onChange={(event) => update("follow_up_after_hours", event.target.value)}
              disabled={disabled || !form.follow_up_enabled}
              error={errors.follow_up_after_hours}
              containerClassName="sm:max-w-xs"
              hint="Через сколько часов тишины написать. Больше 23 нельзя: через сутки Instagram уже не пропустит сообщение"
            />
          </div>
        </SectionCard>

        <SectionCard title="Кухня / склад" description="Точка отправления для маршрута доставки">
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Input
                label="Название"
                value={form.warehouse_name}
                onChange={(event) => update("warehouse_name", event.target.value)}
                disabled={disabled}
              />
              <Input
                label="Адрес"
                value={form.warehouse_address}
                onChange={(event) => update("warehouse_address", event.target.value)}
                disabled={disabled}
              />
            </div>
            <WarehouseMapField
              latitude={form.warehouse_latitude}
              longitude={form.warehouse_longitude}
              onChange={(lat, lng) => {
                update("warehouse_latitude", lat);
                update("warehouse_longitude", lng);
              }}
              disabled={disabled}
            />
          </div>
        </SectionCard>

        <SectionCard title="Самовывоз и сроки" description="Адрес самовывоза и допустимые сроки заказа">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Input
              label="Адрес самовывоза"
              value={form.pickup_address}
              onChange={(event) => update("pickup_address", event.target.value)}
              disabled={disabled}
              containerClassName="sm:col-span-2"
            />
            <Input
              label="Часы работы"
              value={form.working_hours}
              onChange={(event) => update("working_hours", event.target.value)}
              disabled={disabled}
              containerClassName="sm:col-span-2"
              hint='Например: "09:00–19:00 без выходных". Этот текст бот показывает клиентам'
            />
            <TimeInput
              label="Выдача заказов с"
              value={form.order_hours_start}
              onValueChange={(value) => update("order_hours_start", value)}
              disabled={disabled}
              hint="Раньше этого времени бот заказ не примет. Пусто — без ограничения"
            />
            <TimeInput
              label="Выдача заказов до"
              value={form.order_hours_end}
              onValueChange={(value) => update("order_hours_end", value)}
              disabled={disabled}
              error={errors.order_hours_end}
              hint="Позже этого времени бот заказ не примет. Пусто — без ограничения"
            />
            <Input
              label="Минимальный срок предзаказа, ч"
              type="number"
              min="0"
              step="1"
              value={form.min_lead_time_hours}
              onChange={(event) => update("min_lead_time_hours", event.target.value)}
              disabled={disabled}
              error={errors.min_lead_time_hours}
            />
            <Input
              label="Максимум дней вперёд"
              type="number"
              min="0"
              step="1"
              value={form.max_days_ahead}
              onChange={(event) => update("max_days_ahead", event.target.value)}
              disabled={disabled}
              error={errors.max_days_ahead}
            />
            <div className="sm:col-span-2">
              <span className="mb-2 block text-sm font-medium text-slate-700">Выходные дни</span>
              <div className="flex flex-wrap gap-2">
                {WEEKDAYS.map((label, index) => {
                  const active = form.closed_weekdays.includes(index);
                  return (
                    <button
                      key={label}
                      type="button"
                      disabled={disabled}
                      aria-pressed={active}
                      onClick={() =>
                        update(
                          "closed_weekdays",
                          active
                            ? form.closed_weekdays.filter((day) => day !== index)
                            : [...form.closed_weekdays, index],
                        )
                      }
                      className={`rounded-md border px-3 py-1.5 text-sm transition disabled:cursor-not-allowed disabled:opacity-60 ${
                        active
                          ? "border-rose-300 bg-rose-50 text-rose-700"
                          : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50"
                      }`}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
              <p className="mt-1.5 text-xs text-slate-500">
                В отмеченные дни бот заказы не принимает и предлагает ближайший рабочий день
              </p>
            </div>
            <Input
              label="Минимальный заказ, шт"
              type="number"
              min="1"
              step="1"
              value={form.min_order_quantity}
              onChange={(event) => update("min_order_quantity", event.target.value)}
              disabled={disabled}
              error={errors.min_order_quantity}
              hint="Меньше этого количества бот заказ не примет. 1 — без ограничения"
            />
            <Input
              label="Кратность заказа, шт"
              type="number"
              min="1"
              step="1"
              value={form.order_quantity_step}
              onChange={(event) => update("order_quantity_step", event.target.value)}
              disabled={disabled}
              error={errors.order_quantity_step}
              hint="Шаг количества: заказ будет кратен этому числу. Коробочки по 4 и по 6 — это минимум 4 и шаг 2. 1 — без ограничения"
            />
          </div>
        </SectionCard>

        <SectionCard title="Доставка и маршрут" description="Параметры окна доставки и построения маршрута">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Input
              label="Окно доставки, мин"
              type="number"
              min="0"
              step="5"
              value={form.delivery_time_window_minutes}
              onChange={(event) => update("delivery_time_window_minutes", event.target.value)}
              disabled={disabled}
              error={errors.delivery_time_window_minutes}
            />
            <TimeInput
              label="Время старта маршрута"
              value={form.route_start_time}
              onValueChange={(value) => update("route_start_time", value)}
              disabled={disabled}
            />
            <Input
              label="Время на точке, мин"
              type="number"
              min="0"
              step="1"
              value={form.service_time_minutes}
              onChange={(event) => update("service_time_minutes", event.target.value)}
              disabled={disabled}
              error={errors.service_time_minutes}
            />
            <Input
              label="Средняя скорость, км/ч"
              type="number"
              min="0"
              step="1"
              value={form.average_speed_kmh}
              onChange={(event) => update("average_speed_kmh", event.target.value)}
              disabled={disabled}
              error={errors.average_speed_kmh}
            />
          </div>
        </SectionCard>

        <SectionCard title="Отчёты" description="Время формирования ежедневного отчёта">
          <TimeInput
            label="Время ежедневного отчёта"
            value={form.daily_report_time}
            onValueChange={(value) => update("daily_report_time", value)}
            disabled={disabled}
          />
        </SectionCard>

        <SectionCard title="Тексты для бота" description="Эти тексты бот использует при ответах клиентам">
          <div className="flex flex-col gap-4">
            <Textarea
              label="Способы оплаты"
              rows={3}
              value={form.payment_methods_text}
              onChange={(event) => update("payment_methods_text", event.target.value)}
              disabled={disabled}
            />
            <Textarea
              label="Информация о доставке"
              rows={3}
              value={form.delivery_info_text}
              onChange={(event) => update("delivery_info_text", event.target.value)}
              disabled={disabled}
            />
          </div>
        </SectionCard>

        <SectionCard
          title="Предоплата"
          description="После подтверждения заказа бот просит перевести предоплату на кошелёк или карту и прислать чек; чек бот читает сам и сверяет сумму и получателя"
        >
          <div className="flex flex-col gap-4">
            <Switch
              checked={form.prepayment_enabled}
              onCheckedChange={(value) => update("prepayment_enabled", value)}
              disabled={disabled}
              label="Просить предоплату"
              description="Выключено — бот про оплату ничего не просит, оплату отмечает сотрудник в заказе."
            />
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Input
                label="Номер кошелька или карты"
                value={form.prepayment_wallet}
                onChange={(event) => update("prepayment_wallet", event.target.value)}
                disabled={disabled}
                error={errors.prepayment_wallet}
                hint="Например: +992 92 757 53 33 или 5058 2703 8115 6297 — бот сам скажет «кошелёк» или «карта»"
              />
              <Input
                label="Где принимается"
                value={form.prepayment_wallet_banks}
                onChange={(event) => update("prepayment_wallet_banks", event.target.value)}
                disabled={disabled}
                hint='Например: "Душанбе Сити, Алиф, Эсхата" или банк карты; пусто — бот не уточняет'
              />
              <Input
                label="Доля предоплаты, %"
                type="number"
                min="1"
                max="100"
                step="1"
                value={form.prepayment_percent}
                onChange={(event) => update("prepayment_percent", event.target.value)}
                disabled={disabled}
                error={errors.prepayment_percent}
                hint="100 — полная оплата вперёд"
              />
            </div>
            <Switch
              checked={form.prepayment_auto_confirm}
              onCheckedChange={(value) => update("prepayment_auto_confirm", value)}
              disabled={disabled}
              label="Отмечать оплату по чеку автоматически"
              description="Выключено (рекомендуется): бот читает чек и пишет клиенту результат, а оплату в заказе подтверждает сотрудник после сверки в приложении банка. Включено: если сумма и получатель на чеке совпали, заказ сразу помечается оплаченным — скриншот можно подделать."
            />
          </div>
        </SectionCard>

        {isAdmin ? <IntegrationsPanel /> : null}
      </div>
    </>
  );
}
