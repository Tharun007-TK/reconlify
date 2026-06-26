"use client";

import React, { useState, useCallback } from "react";
import { useForm, Controller } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { useDropzone, FileRejection } from "react-dropzone";
import axios from "axios";
import { 
  UploadCloud, 
  File as FileIcon, 
  X, 
  CheckCircle2, 
  AlertCircle, 
  Loader2 
} from "lucide-react";
import { cn } from "@/lib/utils";

// --- Types & Schema ---

const uploadSchema = z.object({
  purchase_register: z.custom<File>((v) => {
    if (typeof window === "undefined") return true;
    return v instanceof File;
  }, {
    message: "Purchase Register file is required",
  }),
  gstr2b: z.custom<File>((v) => {
    if (typeof window === "undefined") return true;
    return v instanceof File;
  }, {
    message: "GSTR-2B file is required",
  }),
});

type UploadFormValues = z.infer<typeof uploadSchema>;

const ACCEPTED_FILE_TYPES = {
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
  "application/vnd.ms-excel": [".xls"],
  "text/csv": [".csv"],
};

// --- Helper Functions ---

function formatBytes(bytes: number, decimals = 2) {
  if (!+bytes) return "0 Bytes";
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ["Bytes", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
}

// --- Dropzone Component ---

interface FileDropzoneProps {
  name: string;
  title: string;
  file: File | null;
  error?: string;
  onChange: (file: File | null) => void;
}

function FileDropzone({ title, file, error, onChange }: FileDropzoneProps) {
  const onDrop = useCallback(
    (acceptedFiles: File[], rejectedFiles: FileRejection[]) => {
      if (acceptedFiles.length > 0) {
        onChange(acceptedFiles[0]);
      }
      if (rejectedFiles.length > 0) {
        // We could handle rejection messages here
        console.warn("File rejected:", rejectedFiles[0].errors);
      }
    },
    [onChange]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: ACCEPTED_FILE_TYPES,
    maxFiles: 1,
    multiple: false,
  });

  return (
    <div className="rounded-xl border bg-card text-card-foreground shadow flex flex-col h-full">
      <div className="p-6 pb-4">
        <h3 className="font-semibold leading-none tracking-tight">{title}</h3>
      </div>
      <div className="p-6 pt-0 flex-1 flex flex-col">
        {!file ? (
          <div
            {...getRootProps()}
            className={cn(
              "flex-1 border-2 border-dashed rounded-lg flex flex-col items-center justify-center p-6 text-center cursor-pointer transition-colors min-h-[200px]",
              isDragActive
                ? "border-primary bg-primary/5"
                : "border-muted-foreground/25 hover:bg-muted/50",
              error && "border-destructive/50 hover:bg-destructive/5"
            )}
          >
            <input {...getInputProps()} />
            <div className="rounded-full bg-muted p-3 mb-4">
              <UploadCloud className="h-6 w-6 text-muted-foreground" />
            </div>
            <p className="text-sm font-medium mb-1">
              Click to select or drag and drop
            </p>
            <p className="text-xs text-muted-foreground mb-4">
              XLSX, XLS, or CSV (max 50MB)
            </p>
            {error && <p className="text-sm text-destructive font-medium mt-2">{error}</p>}
          </div>
        ) : (
          <div className="flex-1 border rounded-lg p-4 flex flex-col min-h-[200px] justify-between bg-muted/20">
            <div className="flex items-start justify-between">
              <div className="flex items-center space-x-3">
                <div className="bg-primary/10 p-2 rounded-md">
                  <FileIcon className="h-6 w-6 text-primary" />
                </div>
                <div>
                  <p className="text-sm font-medium line-clamp-1 break-all" title={file.name}>
                    {file.name}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {formatBytes(file.size)}
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => onChange(null)}
                className="text-muted-foreground hover:text-foreground transition-colors"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="mt-4 flex items-center text-sm font-medium text-green-600 dark:text-green-500">
              <CheckCircle2 className="h-4 w-4 mr-2" />
              Ready to upload
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// --- Main Page Component ---

export default function UploadsPage() {
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<{
    success: boolean;
    message: string;
    details?: any;
  } | null>(null);

  const {
    control,
    handleSubmit,
    formState: { errors, isValid },
    reset,
  } = useForm<UploadFormValues>({
    resolver: zodResolver(uploadSchema),
    defaultValues: {
      purchase_register: undefined,
      gstr2b: undefined,
    },
    mode: "onChange",
  });

  const onSubmit = async (data: UploadFormValues) => {
    setIsUploading(true);
    setUploadProgress(0);
    setUploadResult(null);

    // Generate a temporary project ID for the session
    const projectId = crypto.randomUUID();

    try {
      // 1. Upload Purchase Register
      const prFormData = new FormData();
      prFormData.append("project_id", projectId);
      prFormData.append("file_type", "purchase_register");
      prFormData.append("file", data.purchase_register);

      const prResponse = await axios.post("http://localhost:8000/api/v1/upload/direct", prFormData, {
        headers: { "Content-Type": "multipart/form-data" },
        onUploadProgress: (progressEvent) => {
          if (progressEvent.total) {
            setUploadProgress(Math.round((progressEvent.loaded * 40) / progressEvent.total));
          }
        },
      });
      const uploadPrId = prResponse.data.upload_id;

      // 2. Upload GSTR-2B
      const g2bFormData = new FormData();
      g2bFormData.append("project_id", projectId);
      g2bFormData.append("file_type", "gstr_2b");
      g2bFormData.append("file", data.gstr2b);

      const g2bResponse = await axios.post("http://localhost:8000/api/v1/upload/direct", g2bFormData, {
        headers: { "Content-Type": "multipart/form-data" },
        onUploadProgress: (progressEvent) => {
          if (progressEvent.total) {
            setUploadProgress(40 + Math.round((progressEvent.loaded * 40) / progressEvent.total));
          }
        },
      });
      const upload2bId = g2bResponse.data.upload_id;

      setUploadProgress(90);

      // 3. Confirm Uploads
      const confirmResponse = await axios.post("http://localhost:8000/api/v1/upload/confirm", {
        project_id: projectId,
        upload_pr_id: uploadPrId,
        upload_2b_id: upload2bId,
        run_config: {}
      });

      setUploadResult({
        success: true,
        message: confirmResponse.data.message || "Files uploaded successfully! Reconciliation has been queued.",
        details: confirmResponse.data,
      });
      setUploadProgress(100);

    } catch (error: any) {
      console.error("Upload failed:", error);
      setUploadResult({
        success: false,
        message: error.response?.data?.detail || error.message || "Failed to upload files. Please try again.",
      });
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="flex-1 space-y-6 p-8 pt-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between space-y-2">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">Upload Data</h2>
          <p className="text-muted-foreground mt-1">
            Upload your Purchase Register and GSTR-2B to start the reconciliation process.
          </p>
        </div>
      </div>

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
        <div className="grid gap-6 md:grid-cols-2">
          {/* PR Dropzone */}
          <Controller
            control={control}
            name="purchase_register"
            render={({ field }) => (
              <FileDropzone
                name={field.name}
                title="Purchase Register"
                file={field.value || null}
                onChange={field.onChange}
                error={errors.purchase_register?.message}
              />
            )}
          />

          {/* GSTR2B Dropzone */}
          <Controller
            control={control}
            name="gstr2b"
            render={({ field }) => (
              <FileDropzone
                name={field.name}
                title="GSTR-2B"
                file={field.value || null}
                onChange={field.onChange}
                error={errors.gstr2b?.message}
              />
            )}
          />
        </div>

        {/* Progress & Submit Area */}
        <div className="rounded-xl border bg-card text-card-foreground shadow p-6 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex-1 w-full">
            {isUploading ? (
              <div className="space-y-2">
                <div className="flex justify-between text-sm font-medium">
                  <span>Uploading files...</span>
                  <span>{uploadProgress}%</span>
                </div>
                <div className="h-2 w-full bg-secondary rounded-full overflow-hidden">
                  <div 
                    className="h-full bg-primary transition-all duration-300 ease-in-out" 
                    style={{ width: `${uploadProgress}%` }}
                  />
                </div>
              </div>
            ) : uploadResult ? (
              <div className={cn(
                "flex items-center p-3 rounded-md border",
                uploadResult.success 
                  ? "bg-green-50/50 border-green-200 text-green-900 dark:bg-green-950/20 dark:border-green-900 dark:text-green-400" 
                  : "bg-destructive/10 border-destructive/20 text-destructive"
              )}>
                {uploadResult.success ? (
                  <CheckCircle2 className="h-5 w-5 mr-3 flex-shrink-0" />
                ) : (
                  <AlertCircle className="h-5 w-5 mr-3 flex-shrink-0" />
                )}
                <div>
                  <p className="text-sm font-medium">{uploadResult.message}</p>
                  {uploadResult.details?.upload_batch_id && (
                    <p className="text-xs opacity-80 mt-0.5">Batch ID: {uploadResult.details.upload_batch_id}</p>
                  )}
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                Both files must be valid Excel or CSV files before starting reconciliation.
              </p>
            )}
          </div>
          
          <button
            type="submit"
            disabled={!isValid || isUploading}
            className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground hover:bg-primary/90 h-10 px-8 py-2 w-full sm:w-auto shrink-0"
          >
            {isUploading ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Processing...
              </>
            ) : (
              "Start Reconciliation"
            )}
          </button>
        </div>
      </form>
    </div>
  );
}
