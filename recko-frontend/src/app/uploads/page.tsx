export default function UploadsPage() {
  return (
    <div className="flex-1 space-y-4 p-8 pt-6">
      <div className="flex items-center justify-between space-y-2">
        <h2 className="text-3xl font-bold tracking-tight">Upload Data</h2>
      </div>
      
      <div className="grid gap-6 md:grid-cols-2">
        {/* Drag and Drop Area PR */}
        <div className="rounded-xl border bg-card text-card-foreground shadow">
          <div className="p-6">
            <h3 className="font-semibold leading-none tracking-tight mb-4">Purchase Register</h3>
            <div className="border-2 border-dashed rounded-lg p-12 text-center hover:bg-muted/50 transition-colors cursor-pointer">
              <p className="text-sm text-muted-foreground">Drag and drop your Excel file here, or click to select</p>
            </div>
          </div>
        </div>

        {/* Drag and Drop Area GSTR2B */}
        <div className="rounded-xl border bg-card text-card-foreground shadow">
          <div className="p-6">
            <h3 className="font-semibold leading-none tracking-tight mb-4">GSTR-2B</h3>
            <div className="border-2 border-dashed rounded-lg p-12 text-center hover:bg-muted/50 transition-colors cursor-pointer">
              <p className="text-sm text-muted-foreground">Drag and drop your Excel file here, or click to select</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
