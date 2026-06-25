export default function SettingsPage() {
  return (
    <div className="flex-1 space-y-4 p-8 pt-6">
      <div className="flex items-center justify-between space-y-2">
        <h2 className="text-3xl font-bold tracking-tight">Settings</h2>
      </div>
      
      <div className="rounded-xl border bg-card text-card-foreground shadow max-w-2xl">
        <div className="p-6">
          <h3 className="font-semibold leading-none tracking-tight mb-4">Preferences</h3>
          <div className="space-y-4">
            <div className="flex items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <div className="text-sm font-medium">Dark Mode</div>
                <div className="text-sm text-muted-foreground">Adjust the appearance of the dashboard.</div>
              </div>
              <div>
                {/* Toggle placeholder */}
                <div className="h-6 w-11 rounded-full bg-zinc-200 dark:bg-zinc-800"></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
