import { Card } from "@/components/ui/card";

import { AdminFooter, PulseWordmark } from "../Brand";

export function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <main className="flex flex-1 flex-col items-center justify-center gap-6 p-5">
        <PulseWordmark className="text-4xl" />
        <Card className="w-full max-w-sm border-0 bg-transparent shadow-none">
          {children}
        </Card>
      </main>
      <AdminFooter />
    </div>
  );
}

/** Small "or" divider between OAuth buttons and the email form. */
export function OrDivider(): React.ReactElement {
  return (
    <div className="flex items-center gap-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
      <span className="h-px flex-1 bg-border" />
      or
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}
