import { CustomersView } from "@/components/customers/CustomersView";
import { PageSuspense } from "@/components/shared/Skeletons";

export default function CustomersPage() {
  return (
    <PageSuspense>
      <CustomersView />
    </PageSuspense>
  );
}
