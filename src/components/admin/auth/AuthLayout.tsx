import { Card } from "@/components/ui/card";

export function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <header className="flex items-center justify-center border-b border-border bg-card px-5 py-4">
        <span className="flex items-center gap-2 text-base font-semibold text-foreground">
          <img src="/axiolo-logo.svg" alt="Axiolo" width="72" height="20" />
          <span aria-hidden="true" className="text-muted-foreground">
            ·
          </span>
          Pulse
          <span className="font-normal text-muted-foreground">Admin</span>
        </span>
      </header>
      <main className="flex flex-1 items-center justify-center p-5">
        <Card className="w-full max-w-sm">{children}</Card>
      </main>
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
