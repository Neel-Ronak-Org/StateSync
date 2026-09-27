export function DashboardPlaceholder() {
  return (
    <div className="flex flex-1 flex-col h-full overflow-hidden">
      <header className="flex h-14 items-center justify-between border-b px-6">
        <h1 className="text-sm font-semibold">Dashboard</h1>
      </header>
      <main className="flex flex-1 items-center justify-center p-6">
        <p className="text-sm text-muted-foreground">Pinned answers will appear here.</p>
      </main>
    </div>
  )
}
