import { ChevronLeft } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import { Button } from "@/components/ui/button";

// Placeholder — the full detail (cards, per-recipient responses, recipient
// management, edit/create dialogs, reset/delete, markdown export) lands next.
export function EngagementDetail(): React.ReactElement {
  const { id } = useParams();
  const navigate = useNavigate();
  return (
    <main className="mx-auto w-full max-w-4xl px-4 py-6">
      <Button
        variant="ghost"
        size="sm"
        onClick={() => navigate("/")}
        className="mb-4 gap-1 text-muted-foreground"
      >
        <ChevronLeft />
        All engagements
      </Button>
      <div className="rounded-lg border border-dashed border-border bg-card px-4 py-16 text-center text-sm text-muted-foreground">
        Engagement detail is coming to the new admin shortly.
        <div className="mt-2 text-xs opacity-70">{id}</div>
      </div>
    </main>
  );
}
