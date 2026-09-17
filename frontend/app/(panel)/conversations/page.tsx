import { ConversationsView } from "@/components/conversations/ConversationsView";
import { PageSuspense } from "@/components/shared/Skeletons";

export default function ConversationsPage() {
  return (
    <PageSuspense>
      <ConversationsView />
    </PageSuspense>
  );
}
