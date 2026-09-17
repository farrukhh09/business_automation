"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { CreateUserModal } from "@/components/users/CreateUserModal";
import { EditUserModal } from "@/components/users/EditUserModal";
import { AccessDenied, RoleGate } from "@/components/layout/RoleGate";
import {
  Button,
  ConfirmDialog,
  DataTable,
  EnumBadge,
  IconPlus,
  PageHeader,
  useToast,
  type DataTableColumn,
} from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { USER_ROLE_LABELS, USER_ROLE_TONES } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import { usersApi } from "@/services/api";
import type { UserOut } from "@/types/api";

function UsersPageContent() {
  const { user: currentUser } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();

  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<UserOut | null>(null);
  const [deactivateTarget, setDeactivateTarget] = useState<UserOut | null>(null);

  const {
    data: users,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: queryKeys.users.list(),
    queryFn: () => usersApi.list(),
  });

  const deactivate = useMutation({
    mutationFn: (user: UserOut) => usersApi.deactivate(user.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.users.all });
      toast.success("Пользователь деактивирован");
      setDeactivateTarget(null);
    },
    onError: (err) => {
      // 409 cannot_modify_self and other backend errors already carry the exact Russian message.
      toast.apiError(err);
      setDeactivateTarget(null);
    },
  });

  const columns: DataTableColumn<UserOut>[] = [
    {
      key: "username",
      header: "Логин",
      cell: (user) => (
        <div>
          <div className="font-medium text-slate-900">{user.username}</div>
          {user.id === currentUser?.id ? <div className="text-xs text-slate-400">Это вы</div> : null}
        </div>
      ),
    },
    {
      key: "full_name",
      header: "Полное имя",
      cell: (user) => user.full_name || <span className="text-slate-400">—</span>,
    },
    {
      key: "role",
      header: "Роль",
      cell: (user) => <EnumBadge value={user.role} labels={USER_ROLE_LABELS} tones={USER_ROLE_TONES} />,
    },
    {
      key: "is_active",
      header: "Статус",
      cell: (user) =>
        user.is_active ? (
          <span className="text-emerald-700">Активен</span>
        ) : (
          <span className="text-slate-400">Деактивирован</span>
        ),
    },
    {
      key: "last_login_at",
      header: "Последний вход",
      cell: (user) => formatDateTime(user.last_login_at),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      cell: (user) => (
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setEditTarget(user)}>
            Изменить
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={user.id === currentUser?.id || !user.is_active}
            onClick={() => setDeactivateTarget(user)}
          >
            Деактивировать
          </Button>
        </div>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Пользователи"
        description="Сотрудники панели и их роли"
        actions={
          <Button leftIcon={<IconPlus className="size-4" />} onClick={() => setCreateOpen(true)}>
            Добавить пользователя
          </Button>
        }
      />

      <DataTable
        columns={columns}
        rows={users}
        rowKey={(user) => user.id}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        emptyTitle="Пользователей пока нет"
        emptyDescription="Добавьте первого сотрудника панели."
      />

      <CreateUserModal key={createOpen ? "open" : "closed"} open={createOpen} onClose={() => setCreateOpen(false)} />
      <EditUserModal
        key={editTarget?.id ?? "none"}
        open={editTarget !== null}
        onClose={() => setEditTarget(null)}
        user={editTarget}
      />
      <ConfirmDialog
        open={deactivateTarget !== null}
        title="Деактивировать пользователя?"
        description={`Пользователь «${deactivateTarget?.username}» больше не сможет войти в панель. Это действие можно отменить, повторно включив его в редактировании.`}
        tone="danger"
        confirmLabel="Деактивировать"
        loading={deactivate.isPending}
        onConfirm={() => deactivateTarget && deactivate.mutate(deactivateTarget)}
        onCancel={() => setDeactivateTarget(null)}
      />
    </>
  );
}

export default function UsersPage() {
  return (
    <RoleGate roles={["ADMIN"]} fallback={<AccessDenied description="Управление пользователями доступно только администратору." />}>
      <UsersPageContent />
    </RoleGate>
  );
}
