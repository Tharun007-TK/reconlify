export default function ReconciliationPage() {
  return (
    <div className="flex-1 space-y-4 p-8 pt-6">
      <div className="flex items-center justify-between space-y-2">
        <h2 className="text-3xl font-bold tracking-tight">Reconciliation Runs</h2>
      </div>
      
      <div className="rounded-xl border bg-card text-card-foreground shadow">
        <div className="p-6">
          <h3 className="font-semibold leading-none tracking-tight mb-4">Recent Runs</h3>
          <div className="w-full text-sm text-muted-foreground border rounded-md p-8 text-center">
            No reconciliation runs found. Upload files to start a run.
          </div>
        </div>
      </div>
    </div>
  );
}
