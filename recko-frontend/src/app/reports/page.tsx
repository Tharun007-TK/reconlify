export default function ReportsPage() {
  return (
    <div className="flex-1 space-y-4 p-8 pt-6">
      <div className="flex items-center justify-between space-y-2">
        <h2 className="text-3xl font-bold tracking-tight">Reports</h2>
      </div>
      
      <div className="rounded-xl border bg-card text-card-foreground shadow">
        <div className="p-6">
          <h3 className="font-semibold leading-none tracking-tight mb-4">Generated Reports</h3>
          <div className="w-full text-sm text-muted-foreground border rounded-md p-8 text-center">
            No reports available yet. Run a reconciliation to generate reports.
          </div>
        </div>
      </div>
    </div>
  );
}
