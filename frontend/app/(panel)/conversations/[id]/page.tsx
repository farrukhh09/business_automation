import { ConversationDetailView } from "@/components/conversations/ConversationDetailView";

export default async function ConversationDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ConversationDetailView key={id} id={id} />;
}
