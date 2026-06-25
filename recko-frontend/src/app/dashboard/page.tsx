export default function DashboardPage() {
  return (
    <div className="flex-1 space-y-4 p-8 pt-6">
      <div className="flex items-center justify-between space-y-2">
        <h2 className="text-3xl font-bold tracking-tight">Dashboard</h2>
      </div>
      
      {/* KPI Cards Placeholder */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {[
          { title: "Total Records", value: "0" },
          { title: "Matched", value: "0" },
          { title: "Unmatched", value: "0" },
          { title: "Match Rate", value: "0.0%" }
        ].map((kpi, i) => (
          <div key={i} className="rounded-xl border bg-card text-card-foreground shadow">
            <div className="p-6 flex flex-row items-center justify-between space-y-0 pb-2">
              <h3 className="tracking-tight text-sm font-medium">{kpi.title}</h3>
            </div>
            <div className="p-6 pt-0">
              <div className="text-2xl font-bold">{kpi.value}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Charts Placeholder */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-7">
        <div className="col-span-4 rounded-xl border bg-card text-card-foreground shadow">
          <div className="p-6 pb-2"><h3 className="font-semibold leading-none tracking-tight">Monthly Trend</h3></div>
          <div className="p-6 pt-0 flex h-[350px] items-center justify-center text-sm text-muted-foreground">
            Chart data will appear here
          </div>
        </div>
        <div className="col-span-3 rounded-xl border bg-card text-card-foreground shadow">
          <div className="p-6 pb-2"><h3 className="font-semibold leading-none tracking-tight">Mismatch Categories</h3></div>
          <div className="p-6 pt-0 flex h-[350px] items-center justify-center text-sm text-muted-foreground">
            Chart data will appear here
          </div>
        </div>
      </div>
    </div>
  );
}
