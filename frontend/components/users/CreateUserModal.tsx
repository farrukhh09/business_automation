"use client";

import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { PasswordField } from "@/components/users/PasswordField";
import { Button, Input, Modal, Select, useToast } from "@/components/ui";
import { USER_ROLE_LABELS } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { usersApi } from "@/services/api";
import { isApiError } from "@/services/http";
import { USER_ROLES, type UserCreate, type UserRole } from "@/types/api";

const MIN_PASSWORD_LENGTH = 8;

export interface CreateUserModalProps {
  open: boolean;
  onClose: () => void;
}

/**
 * NOTE: the caller must remount this component on each open (e.g. `key={open ? "open" : "closed"}`)
 * so the form starts empty, without a reset-on-open effect.
 */

interface FormState {
  username: string;
  full_name: string;
  password: string;
  role: UserRole;
}

const EMPTY_FORM: FormState = { username: "", full_name: "", password: "", role: "OPERATOR" };

const ROLE_OPTIONS = USER_ROLES.map((role) => ({ value: role, label: USER_ROLE_LABELS[role] }));

/** Create-user modal (ADMIN only). */
export function CreateUserModal({ open, onClose }: CreateUserModalProps) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const mutation = useMutation({
    mutationFn: () => {
      const body: UserCreate = {
        username: form.username.trim(),
        full_name: form.full_name.trim() || null,
        password: form.password,
        role: form.role,
      };
      return usersApi.create(body);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.users.all });
      toast.success("Пользователь создан");
      onClose();
    },
    onError: (error) => {
      if (isApiError(error) && Object.keys(error.fieldErrors).length > 0) setErrors(error.fieldErrors);
      toast.apiError(error, "Не удалось создать пользователя");
    },
  });

  const validate = (): boolean => {
    const nextErrors: Record<string, string> = {};
    if (!form.username.trim()) nextErrors.username = "Укажите логин";
    if (form.password.length < MIN_PASSWORD_LENGTH) {
      nextErrors.password = `Минимум ${MIN_PASSWORD_LENGTH} символов`;
    }
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!validate()) return;
    mutation.mutate();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Новый пользователь"
      dismissible={!mutation.isPending}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>
            Отмена
          </Button>
          <Button onClick={handleSubmit} loading={mutation.isPending}>
            Создать
          </Button>
        </>
      }
    >
      <form className="flex flex-col gap-4" onSubmit={handleSubmit} noValidate>
        <Input
          label="Логин"
          required
          autoComplete="username"
          value={form.username}
          onChange={(event) => setForm((f) => ({ ...f, username: event.target.value }))}
          error={errors.username}
        />
        <Input
          label="Полное имя"
          value={form.full_name}
          onChange={(event) => setForm((f) => ({ ...f, full_name: event.target.value }))}
          error={errors.full_name}
        />
        <PasswordField
          label="Пароль"
          required
          autoComplete="new-password"
          value={form.password}
          onChange={(value) => setForm((f) => ({ ...f, password: value }))}
          error={errors.password}
          hint={`Не менее ${MIN_PASSWORD_LENGTH} символов`}
        />
        <Select
          label="Роль"
          required
          value={form.role}
          onChange={(event) => setForm((f) => ({ ...f, role: event.target.value as UserRole }))}
          options={ROLE_OPTIONS}
          error={errors.role}
        />
      </form>
    </Modal>
  );
}
