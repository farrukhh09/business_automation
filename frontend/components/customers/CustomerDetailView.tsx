"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { CustomerProfileCard } from "@/components/customers/CustomerProfileCard";
import { OrderTable } from "@/components/shared/OrderTable";
import { SectionCard } from "@/components/shared/SectionCard";
import { buttonClasses } from "@/components/ui/Button";
import { IconConversations } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/PageHeader";
import { customerDisplayName } from "@/lib/format";
import { queryKeys } from "@/lib/query";
import { customersApi } from "@/services/api";

export interface CustomerDetailViewProps {
  id: string;
}

export function CustomerDetailView({ id }: CustomerDetailViewProps) {
  const {
    data: customer,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: queryKeys.customers.detail(id),
    queryFn: () => customersApi.get(id),
  });

  const title = customer ? customerDisplayName(customer) : `Клиент #${id}`;

  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[{ label: "Клиенты", href: "/customers" }, { label: title }]}
        actions={
          customer?.conversation_id ? (
            <Link
              href={`/conversations/${customer.conversation_id}`}
              className={buttonClasses({ variant: "outline" })}
            >
              <IconConversations className="size-4" />
              Перейти к диалогу
            </Link>
          ) : null
        }
      />

      <div className="flex flex-col gap-6">
        <CustomerProfileCard customer={customer} loading={isLoading} error={error} onRetry={refetch} />

        <SectionCard title="История заказов">
          <OrderTable
            orders={customer?.orders}
            showCustomer={false}
            loading={isLoading}
            error={error}
            onRetry={refetch}
            emptyTitle="Заказов пока нет"
            emptyDescription="Когда клиент оформит заказ, он появится здесь."
          />
        </SectionCard>
      </div>
    </>
  );
}
