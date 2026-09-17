"use client";

import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { PasswordField } from "@/components/users/PasswordField";
import { Button, Input, Modal, Select, Switch, useToast } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { USER_ROLE_LABELS } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { usersApi } from "@/services/api";
import { isApiError } from "@/services/http";
import { USER_ROLES, type UserOut, type UserRole, type UserUpdate } from "@/types/api";

const MIN_PASSWORD_LENGTH = 8;

export interface EditUserModalProps {
  open: boolean;
  onClose: () => void;
  user: UserOut | null;
}

/**
 * NOTE: the caller must remount this component (e.g. `key={user?.id ?? "none"}`) whenever it is
 * opened for a different user, so the form re-initialises without a reset-on-open effect.
 */

interface FormState {
  full_name: string;
  role: UserRole;
  is_active: boolean;
  password: string;
}

const ROLE_OPTIONS = USER_ROLES.map((role) => ({ value: role, label: USER_ROLE_LABELS[role] }));

function toFormState(user: UserOut | null): FormState {
  return {
    full_name: user?.full_name ?? "",
    role: user?.role ?? "OPERATOR",
    is_active: user?.is_active ?? true,
    password: "",
  };
}

/** Edit-user modal (ADMIN only): name, role, active flag, optional password reset. */
export function EditUserModal({ open, onClose, user }: EditUserModalProps) {
  const { user: currentUser } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState<FormState>(() => toFormState(user));
  const [errors, setErrors] = useState<Record<string, string>>({});

  const isSelf = Boolean(user && currentUser && user.id === currentUser.id);

  const mutation = useMutation({
    mutationFn: () => {
      if (!user) throw new Error("no user");
      const body: UserUpdate = {
        full_name: form.full_name.trim() || null,
        role: form.role,
        is_active: form.is_active,
      };
      if (form.password) body.password = form.password;
      return usersApi.update(user.id, body);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.users.all });
      toast.success("Пользователь обновлён");
      onClose();
    },
    onError: (error) => {
      if (isApiError(error) && Object.keys(error.fieldErrors).length > 0) setErrors(error.fieldErrors);
      // 409 cannot_modify_self and other backend errors already carry the exact Russian message.
      toast.apiError(error);
    },
  });

  const validate = (): boolean => {
    const nextErrors: Record<string, string> = {};
    if (form.password && form.password.length < MIN_PASSWORD_LENGTH) {
      nextErrors.password = `Минимум ${MIN_PASSWORD_LENGTH} символов`;
    }
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!user) return;
    if (!validate()) return;
    mutation.mutate();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={user ? `Пользователь: ${user.username}` : "Пользователь"}
      dismissible={!mutation.isPending}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>
            Отмена
          </Button>
          <Button onClick={handleSubmit} loading={mutation.isPending}>
            Сохранить
          </Button>
        </>
      }
    >
      <form className="flex flex-col gap-4" onSubmit={handleSubmit} noValidate>
        <Input
          label="Полное имя"
          value={form.full_name}
          onChange={(event) => setForm((f) => ({ ...f, full_name: event.target.value }))}
          error={errors.full_name}
        />
        <Select
          label="Роль"
          required
          value={form.role}
          onChange={(event) => setForm((f) => ({ ...f, role: event.target.value as UserRole }))}
          options={ROLE_OPTIONS}
          error={errors.role}
          disabled={isSelf}
          hint={isSelf ? "Нельзя изменить роль самому себе" : undefined}
        />
        <Switch
          checked={form.is_active}
          onCheckedChange={(value) => setForm((f) => ({ ...f, is_active: value }))}
          disabled={isSelf}
          label="Активен"
          description={isSelf ? "Нельзя деактивировать самого себя" : "Неактивный пользователь не может войти в систему"}
        />
        <PasswordField
          label="Новый пароль"
          autoComplete="new-password"
          value={form.password}
          onChange={(value) => setForm((f) => ({ ...f, password: value }))}
          error={errors.password}
          hint={`Оставьте пустым, чтобы не менять пароль. Новый пароль — не менее ${MIN_PASSWORD_LENGTH} символов.`}
        />
      </form>
    </Modal>
  );
}
