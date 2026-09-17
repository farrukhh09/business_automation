"use client";

import { useQuery } from "@tanstack/react-query";

import { CopyButton } from "@/components/shared/CopyButton";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Spinner } from "@/components/ui/Spinner";
import { ErrorState } from "@/components/ui/ErrorState";
import { IconPrinter } from "@/components/ui/icons";
import { queryKeys } from "@/lib/query";
import { deliveriesApi } from "@/services/api";

export interface DispatchSheetModalProps {
  date: string;
  open: boolean;
  onClose: () => void;
}

/** "Лист для Maxim" — GET /deliveries/dispatch-sheet: a copyable, printable stop list in route order. */
export function DispatchSheetModal({ date, open, onClose }: DispatchSheetModalProps) {
  const query = useQuery({
    queryKey: queryKeys.deliveries.dispatchSheet(date),
    queryFn: () => deliveriesApi.dispatchSheet({ date }),
    enabled: open,
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Лист для Maxim"
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Закрыть
          </Button>
          {query.data ? (
            <>
              <Button
                variant="outline"
                leftIcon={<IconPrinter className="size-4" />}
                onClick={() => window.print()}
                className="no-print"
              >
                Печать
              </Button>
              <CopyButton text={query.data.text} label="Скопировать список" />
            </>
          ) : null}
        </>
      }
    >
      {query.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner label="Формируем список…" />
        </div>
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : (
        <pre className="font-mono text-sm whitespace-pre-wrap text-slate-800">{query.data?.text}</pre>
      )}
    </Modal>
  );
}
