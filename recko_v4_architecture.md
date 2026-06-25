# Recko v4 — GST Reconciliation & Audit Intelligence Platform
## Complete Production-Ready Architecture

---

## Table of Contents
1. [System Overview](#system-overview)
2. [High-Level Architecture Diagram](#high-level-architecture-diagram)
3. [Folder Structure](#folder-structure)
4. [API Structure](#api-structure)
5. [Database Schema](#database-schema)
6. [Background Jobs](#background-jobs)
7. [Security Architecture](#security-architecture)
8. [Storage Architecture](#storage-architecture)
9. [Deployment Architecture](#deployment-architecture)
10. [Core Workflow Pipeline](#core-workflow-pipeline)

---

## 1. System Overview

Recko v4 is a **multi-tenant SaaS platform** that automates GST reconciliation by:
- Ingesting Purchase Registers and GSTR-2B data
- Running Reconlify CLI for reconciliation
- Detecting duplicates and mismatch categories
- Providing vendor-level analysis and audit-grade reports

### User Roles
| Role | Access Scope |
|---|---|
| **Auditor** | Assigned reconciliation jobs, read/write on own firm's data |
| **CA Firm Admin** | Manages firm users, all jobs within firm, billing |
| **Internal Admin** | Full platform access, tenant management, system health |

---

## 2. High-Level Architecture Diagram

```mermaid
graph TB
    subgraph CLIENT["Client Layer (Next.js 15)"]
        UI[Dashboard / Upload / Reports]
        AUTH_UI[Auth Pages]
    end

    subgraph EDGE["Edge Layer (Vercel)"]
        MW[Next.js Middleware - Auth Guard]
        API_ROUTES[Next.js API Routes - BFF]
    end

    subgraph BACKEND["Backend Layer (FastAPI)"]
        UPLOAD_SVC[Upload Service]
        PARSE_SVC[Parse Service]
        RECON_SVC[Reconciliation Orchestrator]
        REPORT_SVC[Report Generator]
        ADMIN_SVC[Admin Service]
        WEBHOOK_SVC[Webhook Handler]
    end

    subgraph WORKERS["Worker Layer"]
        Q[ARQ / Redis Queue]
        W1[Parse Worker]
        W2[Recon Worker - Reconlify CLI]
        W3[Report Worker]
        W4[Notification Worker]
    end

    subgraph DATA["Data Layer"]
        PG[(Supabase PostgreSQL)]
        STORAGE[(Supabase Storage)]
        REDIS[(Redis - Cache + Queue)]
        REALTIME[Supabase Realtime]
    end

    subgraph INFRA["Infrastructure"]
        SUPABASE_AUTH[Supabase Auth]
        CRON[Supabase Edge Cron]
        SENTRY[Sentry]
        POSTHOG[PostHog Analytics]
    end

    UI --> MW
    MW --> API_ROUTES
    API_ROUTES --> BACKEND
    BACKEND --> Q
    Q --> W1 & W2 & W3 & W4
    W1 & W2 & W3 --> PG
    W1 & W2 --> STORAGE
    W3 --> STORAGE
    BACKEND --> PG
    BACKEND --> STORAGE
    BACKEND --> REALTIME
    UI --> REALTIME
    AUTH_UI --> SUPABASE_AUTH
    SUPABASE_AUTH --> MW
```

---

## 3. Folder Structure

### 3.1 Frontend — Next.js 15 App Router

```
recko-web/
├── app/
│   ├── (auth)/
│   │   ├── login/
│   │   │   └── page.tsx
│   │   ├── signup/
│   │   │   └── page.tsx
│   │   └── layout.tsx
│   ├── (dashboard)/
│   │   ├── layout.tsx                    # Sidebar + Header shell
│   │   ├── page.tsx                      # Dashboard home
│   │   ├── reconciliations/
│   │   │   ├── page.tsx                  # All jobs list
│   │   │   ├── new/
│   │   │   │   └── page.tsx              # Upload wizard
│   │   │   └── [jobId]/
│   │   │       ├── page.tsx              # Job overview
│   │   │       ├── mismatches/
│   │   │       │   └── page.tsx          # Mismatch table
│   │   │       ├── duplicates/
│   │   │       │   └── page.tsx          # Duplicate flags
│   │   │       ├── vendors/
│   │   │       │   └── page.tsx          # Vendor analysis
│   │   │       └── reports/
│   │   │           └── page.tsx          # Report download
│   │   ├── vendors/
│   │   │   └── page.tsx                  # Vendor master
│   │   ├── audit-trail/
│   │   │   └── page.tsx
│   │   ├── settings/
│   │   │   ├── profile/page.tsx
│   │   │   ├── firm/page.tsx
│   │   │   ├── users/page.tsx
│   │   │   └── billing/page.tsx
│   │   └── admin/                        # Internal admin only
│   │       ├── tenants/page.tsx
│   │       ├── jobs/page.tsx
│   │       └── system/page.tsx
│   ├── api/                              # BFF (Backend-for-Frontend)
│   │   ├── auth/
│   │   │   └── callback/route.ts
│   │   ├── jobs/
│   │   │   ├── route.ts                  # GET list, POST create
│   │   │   └── [jobId]/
│   │   │       ├── route.ts
│   │   │       └── status/route.ts
│   │   ├── upload/
│   │   │   └── presign/route.ts          # Presigned URL issuer
│   │   ├── reports/
│   │   │   └── [jobId]/route.ts
│   │   └── webhooks/
│   │       └── fastapi/route.ts          # Internal webhook receiver
│   ├── globals.css
│   └── layout.tsx
│
├── components/
│   ├── ui/                               # Shadcn UI base components
│   ├── layout/
│   │   ├── Sidebar.tsx
│   │   ├── Header.tsx
│   │   └── TenantSwitcher.tsx
│   ├── reconciliation/
│   │   ├── UploadWizard.tsx
│   │   ├── JobStatusBadge.tsx
│   │   ├── JobProgressRing.tsx
│   │   ├── MismatchTable.tsx
│   │   ├── DuplicateTable.tsx
│   │   └── ReconSummaryCard.tsx
│   ├── vendors/
│   │   ├── VendorRiskBadge.tsx
│   │   └── VendorStatsChart.tsx
│   ├── charts/
│   │   ├── MismatchBreakdownChart.tsx
│   │   └── TaxExposureChart.tsx
│   └── shared/
│       ├── DataTable.tsx
│       ├── FileDropzone.tsx
│       ├── RealtimeJobTracker.tsx
│       └── ConfirmDialog.tsx
│
├── hooks/
│   ├── useReconJob.ts
│   ├── useJobRealtime.ts
│   ├── usePresignedUpload.ts
│   └── useTenant.ts
│
├── lib/
│   ├── supabase/
│   │   ├── client.ts                     # Browser client
│   │   ├── server.ts                     # Server client (RSC)
│   │   └── middleware.ts
│   ├── api/
│   │   ├── jobs.ts                       # Typed API client
│   │   ├── reports.ts
│   │   └── vendors.ts
│   ├── validations/
│   │   ├── upload.schema.ts
│   │   └── job.schema.ts
│   └── utils/
│       ├── formatters.ts
│       └── gst.ts                        # GSTIN validators, formatters
│
├── middleware.ts                          # Auth guard + tenant routing
├── types/
│   ├── database.types.ts                 # Supabase generated types
│   ├── api.types.ts
│   └── domain.types.ts
│
└── config/
    ├── site.ts
    └── nav.ts
```

---

### 3.2 Backend — FastAPI

```
recko-api/
├── app/
│   ├── main.py                           # FastAPI app factory
│   ├── config.py                         # Settings via pydantic-settings
│   ├── dependencies.py                   # Shared DI (db, auth, tenant)
│   │
│   ├── api/
│   │   └── v1/
│   │       ├── router.py                 # Aggregates all routers
│   │       ├── auth/
│   │       │   └── router.py             # JWT validation endpoints
│   │       ├── jobs/
│   │       │   ├── router.py
│   │       │   ├── schemas.py
│   │       │   └── service.py
│   │       ├── upload/
│   │       │   ├── router.py
│   │       │   └── service.py
│   │       ├── reconciliation/
│   │       │   ├── router.py
│   │       │   ├── schemas.py
│   │       │   └── service.py
│   │       ├── vendors/
│   │       │   ├── router.py
│   │       │   └── service.py
│   │       ├── reports/
│   │       │   ├── router.py
│   │       │   └── service.py
│   │       ├── admin/
│   │       │   ├── router.py
│   │       │   └── service.py
│   │       └── webhooks/
│   │           └── router.py
│   │
│   ├── core/
│   │   ├── security.py                   # JWT decode, RBAC
│   │   ├── tenant.py                     # Tenant context resolution
│   │   ├── exceptions.py
│   │   └── logging.py
│   │
│   ├── db/
│   │   ├── session.py                    # AsyncPG connection pool
│   │   └── repositories/
│   │       ├── base.py
│   │       ├── jobs_repo.py
│   │       ├── mismatches_repo.py
│   │       ├── vendors_repo.py
│   │       └── tenants_repo.py
│   │
│   ├── workers/
│   │   ├── queue.py                      # ARQ worker setup
│   │   ├── tasks/
│   │   │   ├── parse_task.py             # Parse PR + 2B files
│   │   │   ├── recon_task.py             # Run Reconlify CLI
│   │   │   ├── report_task.py            # Generate Excel/PDF reports
│   │   │   └── notify_task.py            # Email / webhook notify
│   │   └── worker_settings.py
│   │
│   ├── services/
│   │   ├── parser/
│   │   │   ├── purchase_register.py      # Pandas parser for PR
│   │   │   ├── gstr2b.py                 # Pandas parser for GSTR-2B JSON/Excel
│   │   │   └── normalizer.py             # Canonical field mapping
│   │   ├── reconciliation/
│   │   │   ├── reconlify_runner.py       # subprocess wrapper for Reconlify CLI
│   │   │   ├── mismatch_classifier.py    # Rule-based mismatch categories
│   │   │   └── duplicate_detector.py     # Fuzzy + exact duplicate logic
│   │   ├── vendor/
│   │   │   ├── risk_scorer.py
│   │   │   └── aggregator.py
│   │   ├── report/
│   │   │   ├── excel_generator.py        # OpenPyXL templates
│   │   │   └── pdf_generator.py          # WeasyPrint / Jinja2
│   │   └── storage/
│   │       └── supabase_storage.py       # Upload/download wrappers
│   │
│   └── models/
│       ├── job.py
│       ├── tenant.py
│       ├── vendor.py
│       └── mismatch.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── alembic/                              # DB migrations
│   ├── versions/
│   └── env.py
│
├── scripts/
│   ├── seed_dev.py
│   └── run_worker.py
│
├── pyproject.toml
├── Dockerfile
└── docker-compose.dev.yml
```

---

## 4. API Structure

### 4.1 API Versioning Strategy

All endpoints live under `/api/v1/`. The Next.js BFF proxies authenticated requests to FastAPI. Internal service-to-service calls use a shared secret header.

```
Base URL: https://api.recko.app/api/v1
Auth: Bearer <supabase_jwt>
Tenant: X-Tenant-ID: <tenant_id>  (resolved from JWT claims)
```

---

### 4.2 Endpoint Reference

#### Jobs

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `GET` | `/jobs` | List all jobs (paginated, filtered) | Auditor+ |
| `POST` | `/jobs` | Create new recon job | Auditor+ |
| `GET` | `/jobs/{job_id}` | Job detail + status | Auditor+ |
| `DELETE` | `/jobs/{job_id}` | Soft-delete job | CA Admin |
| `GET` | `/jobs/{job_id}/status` | Realtime polling fallback | Auditor+ |
| `POST` | `/jobs/{job_id}/retry` | Re-queue failed job | CA Admin |

#### Upload

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `POST` | `/upload/presign` | Issue presigned URL for direct S3 upload | Auditor+ |
| `POST` | `/upload/confirm` | Confirm upload, trigger parse pipeline | Auditor+ |

#### Reconciliation

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `GET` | `/recon/{job_id}/summary` | Reconciliation summary stats | Auditor+ |
| `GET` | `/recon/{job_id}/mismatches` | Paginated mismatch records | Auditor+ |
| `GET` | `/recon/{job_id}/duplicates` | Detected duplicates | Auditor+ |
| `PATCH` | `/recon/{job_id}/mismatches/{id}` | Annotate / resolve mismatch | Auditor+ |
| `GET` | `/recon/{job_id}/vendors` | Per-vendor reconciliation breakdown | Auditor+ |

#### Reports

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `POST` | `/reports/{job_id}/generate` | Queue report generation | Auditor+ |
| `GET` | `/reports/{job_id}` | List generated reports | Auditor+ |
| `GET` | `/reports/{job_id}/download` | Signed download URL | Auditor+ |

#### Admin (Internal Only)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `GET` | `/admin/tenants` | List all tenants | Admin |
| `POST` | `/admin/tenants` | Create tenant | Admin |
| `GET` | `/admin/tenants/{id}/jobs` | All jobs for tenant | Admin |
| `GET` | `/admin/system/health` | Queue + DB health | Admin |
| `GET` | `/admin/system/metrics` | Usage metrics | Admin |

---

### 4.3 API Pipeline Flow (Job Creation)

```mermaid
sequenceDiagram
    participant FE as Next.js Frontend
    participant BFF as Next.js BFF Route
    participant FA as FastAPI
    participant Q as ARQ Queue
    participant W as Worker
    participant RT as Supabase Realtime

    FE->>BFF: POST /api/jobs (files metadata)
    BFF->>FA: POST /api/v1/upload/presign
    FA-->>BFF: presigned_url[]
    BFF-->>FE: presigned_url[]
    FE->>Supabase Storage: PUT file (direct upload)
    FE->>BFF: POST /api/jobs/confirm
    BFF->>FA: POST /api/v1/upload/confirm
    FA->>Q: enqueue(parse_task, job_id)
    FA-->>BFF: {job_id, status: "queued"}
    BFF-->>FE: {job_id}
    
    W->>Q: dequeue parse_task
    W->>W: Parse PR + GSTR-2B
    W->>Q: enqueue(recon_task)
    W->>RT: UPDATE jobs SET status="parsing"
    
    W->>Q: dequeue recon_task
    W->>W: Run Reconlify CLI
    W->>Q: enqueue(report_task)
    W->>RT: UPDATE jobs SET status="reconciling"
    RT-->>FE: job status push
```

---

## 5. Database Schema

### 5.1 Schema Diagram

```mermaid
erDiagram
    TENANTS {
        uuid id PK
        text name
        text gstin
        text plan
        text status
        jsonb settings
        timestamptz created_at
    }

    USERS {
        uuid id PK
        uuid tenant_id FK
        text email
        text role
        text full_name
        boolean is_active
        timestamptz created_at
    }

    RECON_JOBS {
        uuid id PK
        uuid tenant_id FK
        uuid created_by FK
        text name
        text period
        text status
        jsonb metadata
        integer total_records
        integer matched_count
        integer mismatched_count
        integer duplicate_count
        numeric itc_at_risk
        timestamptz started_at
        timestamptz completed_at
        timestamptz created_at
    }

    JOB_FILES {
        uuid id PK
        uuid job_id FK
        text file_type
        text storage_path
        text original_name
        bigint file_size
        text parse_status
        jsonb parse_error
        timestamptz uploaded_at
    }

    PURCHASE_RECORDS {
        uuid id PK
        uuid job_id FK
        uuid tenant_id FK
        text invoice_number
        text gstin_supplier
        text supplier_name
        date invoice_date
        numeric taxable_value
        numeric igst
        numeric cgst
        numeric sgst
        numeric total_tax
        text fy
        text return_period
        text row_hash
        timestamptz created_at
    }

    GSTR2B_RECORDS {
        uuid id PK
        uuid job_id FK
        uuid tenant_id FK
        text invoice_number
        text gstin_supplier
        text supplier_name
        date invoice_date
        numeric taxable_value
        numeric igst
        numeric cgst
        numeric sgst
        numeric cess
        text fy
        text return_period
        text document_type
        boolean is_amended
        text row_hash
        timestamptz created_at
    }

    MISMATCHES {
        uuid id PK
        uuid job_id FK
        uuid tenant_id FK
        uuid pr_record_id FK
        uuid gstr2b_record_id FK
        text mismatch_type
        text field_name
        text pr_value
        text gstr2b_value
        numeric variance
        text resolution_status
        text resolution_note
        uuid resolved_by FK
        timestamptz resolved_at
        timestamptz created_at
    }

    DUPLICATES {
        uuid id PK
        uuid job_id FK
        text source
        text record_id_a
        text record_id_b
        numeric similarity_score
        text duplicate_type
        timestamptz detected_at
    }

    VENDOR_ANALYSIS {
        uuid id PK
        uuid job_id FK
        uuid tenant_id FK
        text gstin_supplier
        text supplier_name
        integer total_invoices
        integer matched_invoices
        integer mismatched_invoices
        numeric total_itc_claimed
        numeric itc_at_risk
        text risk_level
        jsonb analysis_meta
        timestamptz computed_at
    }

    REPORTS {
        uuid id PK
        uuid job_id FK
        uuid tenant_id FK
        uuid generated_by FK
        text report_type
        text format
        text storage_path
        text status
        timestamptz generated_at
        timestamptz expires_at
    }

    AUDIT_LOGS {
        uuid id PK
        uuid tenant_id FK
        uuid user_id FK
        text action
        text resource_type
        uuid resource_id
        jsonb before_state
        jsonb after_state
        text ip_address
        timestamptz created_at
    }

    TENANTS ||--o{ USERS : has
    TENANTS ||--o{ RECON_JOBS : owns
    USERS ||--o{ RECON_JOBS : creates
    RECON_JOBS ||--o{ JOB_FILES : contains
    RECON_JOBS ||--o{ PURCHASE_RECORDS : has
    RECON_JOBS ||--o{ GSTR2B_RECORDS : has
    RECON_JOBS ||--o{ MISMATCHES : produces
    RECON_JOBS ||--o{ DUPLICATES : produces
    RECON_JOBS ||--o{ VENDOR_ANALYSIS : generates
    RECON_JOBS ||--o{ REPORTS : yields
```

---

### 5.2 SQL: Core Tables

```sql
-- Enable RLS on all tables
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE recon_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE mismatches ENABLE ROW LEVEL SECURITY;

-- Tenant isolation policy (applied to all user-facing tables)
CREATE POLICY "tenant_isolation" ON recon_jobs
    USING (tenant_id = (current_setting('app.current_tenant'))::uuid);

-- Mismatch Categories ENUM
CREATE TYPE mismatch_type AS ENUM (
    'GSTIN_MISMATCH',
    'INVOICE_NUMBER_MISMATCH',
    'AMOUNT_VARIANCE',
    'TAX_RATE_MISMATCH',
    'DATE_MISMATCH',
    'MISSING_IN_2B',
    'MISSING_IN_PR',
    'DUPLICATE_INVOICE'
);

-- Job Status ENUM
CREATE TYPE job_status AS ENUM (
    'queued',
    'uploading',
    'parsing',
    'normalizing',
    'reconciling',
    'analyzing',
    'generating_report',
    'completed',
    'failed',
    'cancelled'
);

-- Resolution Status
CREATE TYPE resolution_status AS ENUM (
    'open',
    'in_review',
    'resolved',
    'disputed',
    'accepted'
);

-- Risk Level
CREATE TYPE risk_level AS ENUM ('low', 'medium', 'high', 'critical');

-- Indexes for query performance
CREATE INDEX idx_recon_jobs_tenant ON recon_jobs(tenant_id, status, created_at DESC);
CREATE INDEX idx_purchase_records_job ON purchase_records(job_id, gstin_supplier);
CREATE INDEX idx_gstr2b_records_job ON gstr2b_records(job_id, gstin_supplier);
CREATE INDEX idx_mismatches_job_type ON mismatches(job_id, mismatch_type, resolution_status);
CREATE INDEX idx_vendor_analysis_job ON vendor_analysis(job_id, risk_level);
CREATE INDEX idx_audit_logs_tenant ON audit_logs(tenant_id, created_at DESC);

-- Unique constraint to prevent duplicate row processing
CREATE UNIQUE INDEX idx_pr_row_hash ON purchase_records(job_id, row_hash);
CREATE UNIQUE INDEX idx_2b_row_hash ON gstr2b_records(job_id, row_hash);
```

---

### 5.3 Row-Level Security Model

```mermaid
graph TD
    subgraph RLS["Supabase RLS Policies"]
        A[JWT Claims] --> B[Extract tenant_id]
        B --> C[set_config app.current_tenant]
        C --> D{Policy Check}
        D -->|tenant_id matches| E[Row Returned]
        D -->|no match| F[Row Hidden]
    end

    subgraph ROLES["RBAC within Tenant"]
        G[auditor] --> H[Read own jobs + Write mismatches]
        I[ca_admin] --> J[All firm jobs + Manage users]
        K[internal_admin] --> L[Cross-tenant access via service role]
    end
```

---

## 6. Background Jobs

### 6.1 Queue Architecture

**Technology**: ARQ (Python async job queue on Redis)

```mermaid
graph LR
    API[FastAPI] -->|enqueue| REDIS[(Redis Streams)]
    REDIS --> PW[Parse Worker]
    REDIS --> RW[Recon Worker]
    REDIS --> RPW[Report Worker]
    REDIS --> NW[Notify Worker]

    PW -->|on_success enqueue| REDIS
    RW -->|on_success enqueue| REDIS

    SCHED[Supabase Cron] -->|scheduled triggers| REDIS
```

---

### 6.2 Task Definitions

#### Task 1: `parse_task`
```
Trigger: POST /upload/confirm
Input: job_id, file_paths[]
Steps:
  1. Download PR file from Supabase Storage
  2. Parse Excel/CSV with Pandas
  3. Normalize fields (GSTIN format, date, amounts)
  4. Compute row_hash (SHA-256 of canonical fields)
  5. Bulk-insert into purchase_records (upsert on row_hash)
  6. Repeat for GSTR-2B (JSON from GST Portal / Excel)
  7. Update job status → "normalizing"
  8. Enqueue recon_task
On failure: Set job status → "failed", store error JSON
Timeout: 10 minutes
Retry: 2x with exponential backoff
```

#### Task 2: `recon_task`
```
Trigger: parse_task success
Input: job_id
Steps:
  1. Export normalized PR + 2B data to temp CSV
  2. Invoke Reconlify CLI:
     reconlify recon --pr /tmp/pr.csv --gstr2b /tmp/2b.csv --out /tmp/out/
  3. Parse output files (matched.csv, mismatches.csv, unmatched.csv)
  4. Run mismatch_classifier.py to categorize mismatches
  5. Run duplicate_detector.py (exact + fuzzy with RapidFuzz)
  6. Bulk-insert into mismatches + duplicates tables
  7. Compute vendor_analysis aggregates
  8. Update recon_jobs summary counts + itc_at_risk
  9. Update job status → "completed"
  10. Enqueue report_task + notify_task
Timeout: 20 minutes
Retry: 1x
```

#### Task 3: `report_task`
```
Trigger: recon_task success
Input: job_id, report_types[]
Steps:
  1. Query all mismatches, vendor_analysis, summary for job
  2. Generate Excel report via OpenPyXL:
     - Sheet 1: Executive Summary
     - Sheet 2: Matched Records
     - Sheet 3: Mismatches (colored by type)
     - Sheet 4: Duplicates
     - Sheet 5: Vendor Risk Table
  3. Generate PDF via Jinja2 + WeasyPrint
  4. Upload to Supabase Storage (reports/{tenant_id}/{job_id}/)
  5. Insert record into reports table with signed URL TTL
  6. Update job status → "report_ready"
Timeout: 5 minutes
```

#### Task 4: `notify_task`
```
Trigger: recon_task success
Input: job_id
Steps:
  1. Fetch job creator email
  2. Send email via Resend API:
     - Summary of mismatches found
     - ITC at risk amount
     - Link to dashboard
  3. Update Supabase Realtime (broadcast to job channel)
  4. If firm has webhook configured: POST to webhook URL
Timeout: 1 minute
```

---

### 6.3 Scheduled Jobs (Supabase Edge Cron)

| Job | Schedule | Purpose |
|-----|----------|---------|
| `cleanup_temp_files` | `0 2 * * *` | Delete processed temp files from storage |
| `expire_reports` | `0 3 * * *` | Remove reports past TTL, update DB |
| `compute_tenant_usage` | `0 0 1 * *` | Monthly billing usage aggregation |
| `requeue_stuck_jobs` | `*/15 * * * *` | Detect and re-queue jobs stuck >30 min |
| `audit_log_archive` | `0 1 * * 0` | Archive old audit logs to cold storage |

---

## 7. Security Architecture

### 7.1 Security Layers

```mermaid
graph TD
    subgraph PERIMETER["Perimeter"]
        CF[Cloudflare WAF + DDoS] --> VERCEL[Vercel Edge]
    end

    subgraph EDGE_SEC["Edge Security"]
        VERCEL --> MW[Next.js Middleware]
        MW --> |Validate Supabase JWT| API[API Routes / FastAPI]
    end

    subgraph APP_SEC["Application Security"]
        API --> TENANT[Tenant Context Injection]
        TENANT --> RBAC[Role-Based Access Control]
        RBAC --> RLS[Supabase Row-Level Security]
    end

    subgraph DATA_SEC["Data Security"]
        RLS --> ENC[Supabase Encrypted at Rest - AES-256]
        API --> TLS[TLS 1.3 in Transit]
        STORAGE[Storage Files] --> SIGNED[Signed URLs - 15 min TTL]
    end

    subgraph AUDIT["Audit & Compliance"]
        API --> AUDITLOG[Audit Log on every mutation]
        AUDITLOG --> PG[(audit_logs table)]
    end
```

---

### 7.2 Authentication Flow

```mermaid
sequenceDiagram
    participant U as User
    participant FE as Next.js
    participant SA as Supabase Auth
    participant MW as Middleware
    participant FA as FastAPI

    U->>FE: Login with email/password (or Google SSO)
    FE->>SA: signInWithPassword()
    SA-->>FE: access_token (JWT) + refresh_token
    FE->>FE: Store in httpOnly cookie via auth-helpers
    U->>FE: Navigate to /reconciliations
    FE->>MW: Request with cookie
    MW->>SA: Verify JWT
    SA-->>MW: Valid | Invalid
    MW->>FA: Forward with Authorization: Bearer <jwt>
    FA->>FA: Decode JWT → extract sub, tenant_id, role
    FA->>FA: Set app.current_tenant in DB session
    FA-->>FE: Response
```

---

### 7.3 Security Controls Checklist

| Control | Implementation |
|---------|----------------|
| **Auth** | Supabase Auth (JWT, refresh tokens, MFA ready) |
| **Session** | httpOnly cookies, 1-hour JWT, 7-day refresh |
| **CSRF** | SameSite=Strict cookies + CSRF token on mutations |
| **XSS** | React + CSP headers via Vercel config |
| **SQL Injection** | Parameterized queries via asyncpg |
| **IDOR** | RLS enforces tenant isolation at DB level |
| **File Validation** | MIME check + file size limit (25MB) + malware scan hook |
| **Rate Limiting** | Upstash Redis rate limiter on upload + auth endpoints |
| **Secrets** | Doppler secrets management (never in .env files) |
| **Audit Trail** | All mutations logged with before/after state |
| **GSTIN PII** | GSTIN treated as PII — masked in logs |
| **Storage Access** | All files accessed via signed URLs (15 min TTL) |
| **API Keys** | Internal service-to-service via shared secret header |

---

### 7.4 Mismatch Classification Rules

```python
# services/reconciliation/mismatch_classifier.py

MISMATCH_RULES = {
    "GSTIN_MISMATCH": lambda pr, b: pr.gstin != b.gstin,
    "AMOUNT_VARIANCE": lambda pr, b: abs(pr.taxable_value - b.taxable_value) > 1.0,
    "TAX_RATE_MISMATCH": lambda pr, b: pr.total_tax_rate != b.total_tax_rate,
    "DATE_MISMATCH": lambda pr, b: pr.invoice_date != b.invoice_date,
    "MISSING_IN_2B": lambda pr, b: b is None,
    "MISSING_IN_PR": lambda pr, b: pr is None,
}

ITC_AT_RISK_RULES = ["MISSING_IN_2B", "GSTIN_MISMATCH", "AMOUNT_VARIANCE"]
```

---

## 8. Storage Architecture

### 8.1 Supabase Storage Bucket Layout

```
supabase-storage/
├── uploads/                              # Raw uploaded files (private)
│   └── {tenant_id}/
│       └── {job_id}/
│           ├── purchase_register.xlsx
│           └── gstr2b.json
│
├── processed/                            # Normalized temp CSVs (private)
│   └── {tenant_id}/
│       └── {job_id}/
│           ├── pr_normalized.csv
│           └── gstr2b_normalized.csv
│
├── reports/                              # Generated reports (private + signed)
│   └── {tenant_id}/
│       └── {job_id}/
│           ├── recon_report_{date}.xlsx
│           └── recon_report_{date}.pdf
│
└── exports/                              # User-initiated data exports (TTL 24h)
    └── {tenant_id}/
        └── {export_id}.csv
```

---

### 8.2 Storage Security Policies

```sql
-- Only authenticated users of the same tenant can access their uploads
CREATE POLICY "tenant_upload_access" ON storage.objects
    FOR ALL USING (
        (storage.foldername(name))[1] = 'uploads' AND
        (storage.foldername(name))[2] = auth.jwt()->>'tenant_id'
    );

-- Reports are accessible only via server-side signed URLs
-- No direct public access
CREATE POLICY "reports_server_only" ON storage.objects
    FOR SELECT USING (false);  -- FastAPI service role bypasses RLS
```

### 8.3 File Lifecycle

```mermaid
graph LR
    A[User Upload] -->|Direct to Storage| B[uploads/ bucket]
    B -->|parse_task downloads| C[Worker Processing]
    C -->|Writes normalized| D[processed/ bucket]
    D -->|recon_task reads| E[Reconlify CLI]
    E -->|report_task generates| F[reports/ bucket]
    F -->|Signed URL 15 min| G[User Download]

    CRON[Daily Cron] -->|30 days| H[Delete processed/]
    CRON -->|90 days| I[Archive reports/ to cold storage]
```

---

## 9. Deployment Architecture

### 9.1 Environment Strategy

| Environment | Frontend | Backend | Database |
|-------------|----------|---------|----------|
| **Development** | `localhost:3000` | `localhost:8000` | Supabase Local / Docker |
| **Staging** | Vercel Preview | Fly.io staging app | Supabase staging project |
| **Production** | Vercel Production | Fly.io production | Supabase production project |

---

### 9.2 Infrastructure Diagram

```mermaid
graph TB
    subgraph CDN["CDN / Edge (Global)"]
        CF[Cloudflare DNS + WAF]
        VE[Vercel Edge Network]
    end

    subgraph FRONTEND["Frontend (Vercel)"]
        NEXT[Next.js 15 App]
    end

    subgraph BACKEND["Backend (Fly.io)"]
        direction TB
        LB[Fly.io Load Balancer]
        API1[FastAPI Instance 1]
        API2[FastAPI Instance 2]
        W1[ARQ Worker 1]
        W2[ARQ Worker 2]
    end

    subgraph DATA["Data Services (Managed)"]
        SUPA[Supabase - PG + Auth + Storage + Realtime]
        REDIS[Upstash Redis - Queue + Cache]
    end

    subgraph OBS["Observability"]
        SENTRY[Sentry - Error Tracking]
        GRAFANA[Grafana + Prometheus]
        POSTHOG[PostHog - Product Analytics]
    end

    CF --> VE --> NEXT
    NEXT --> LB
    LB --> API1 & API2
    API1 & API2 --> REDIS
    REDIS --> W1 & W2
    API1 & API2 & W1 & W2 --> SUPA
    API1 & API2 --> SENTRY
    W1 & W2 --> SENTRY
    API1 & API2 --> GRAFANA
```

---

### 9.3 Fly.io Deployment Config

```toml
# fly.toml (FastAPI)
app = "recko-api"
primary_region = "bom"          # Mumbai for India-first

[build]
  dockerfile = "Dockerfile"

[http_service]
  internal_port = 8000
  force_https = true
  auto_stop_machines = "stop"
  auto_start_machines = true
  min_machines_running = 1

[[vm]]
  memory = "2gb"
  cpu_kind = "performance"
  cpus = 2

[mounts]
  source = "reconlify_tmp"
  destination = "/tmp/reconlify"
```

```toml
# fly.toml (ARQ Workers)
app = "recko-workers"
primary_region = "bom"

[build]
  dockerfile = "Dockerfile.worker"

[[vm]]
  memory = "4gb"          # Workers need more RAM for Pandas + Reconlify
  cpu_kind = "performance"
  cpus = 4
```

---

### 9.4 Docker Configuration

```dockerfile
# Dockerfile (FastAPI)
FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y \
    libpq-dev gcc curl \
    && rm -rf /var/lib/apt/lists/*

# Install Reconlify CLI
RUN curl -sSL https://install.reconlify.io | bash

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[prod]"

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

---

### 9.5 CI/CD Pipeline

```mermaid
graph LR
    subgraph GH["GitHub Actions"]
        PR[Pull Request] --> LINT[Lint + Type Check]
        LINT --> TEST[Unit + Integration Tests]
        TEST --> BUILD[Docker Build]
        BUILD --> DEPLOY_STAGING[Deploy to Staging]
        DEPLOY_STAGING --> E2E[Playwright E2E Tests]
        E2E --> APPROVE{Manual Approval}
        APPROVE --> DEPLOY_PROD[Deploy to Production]
    end
```

```yaml
# .github/workflows/deploy.yml (excerpt)
jobs:
  deploy-api:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: superfly/flyctl-actions/setup-flyctl@master
      - run: flyctl deploy --app recko-api --remote-only
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}

  deploy-workers:
    needs: deploy-api
    runs-on: ubuntu-latest
    steps:
      - uses: superfly/flyctl-actions/setup-flyctl@master
      - run: flyctl deploy --app recko-workers --remote-only
```

---

### 9.6 Environment Variables

```bash
# Frontend (Vercel)
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
NEXT_PUBLIC_API_URL=https://api.recko.app
NEXT_PUBLIC_POSTHOG_KEY=

# Backend (Fly.io via Doppler)
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=       # Bypasses RLS for worker operations
DATABASE_URL=                    # Direct asyncpg connection
REDIS_URL=                       # Upstash Redis
INTERNAL_API_SECRET=             # Service-to-service shared secret
RESEND_API_KEY=                  # Email notifications
SENTRY_DSN=
RECONLIFY_LICENSE_KEY=
```

---

## 10. Core Workflow Pipeline

### 10.1 End-to-End Flow

```mermaid
flowchart TD
    A([User: Upload PR + GSTR-2B]) --> B[Presigned URL Generation]
    B --> C[Direct Upload to Supabase Storage]
    C --> D[Confirm Upload → Create Job Record]
    D --> E[[parse_task queued]]

    E --> F{Parse Purchase Register}
    F -->|Pandas| G[Field Normalization\nGSTIN, Dates, Amounts]
    G --> H[SHA-256 Row Hashing]
    H --> I[(purchase_records table)]

    E --> J{Parse GSTR-2B}
    J -->|JSON / Excel| K[Field Normalization]
    K --> L[SHA-256 Row Hashing]
    L --> M[(gstr2b_records table)]

    I & M --> N[[recon_task queued]]

    N --> O[Export to Temp CSVs]
    O --> P[Reconlify CLI Execution]
    P --> Q[Parse CLI Output]
    Q --> R[Mismatch Classification\n8 categories]
    R --> S[Duplicate Detection\nExact + Fuzzy RapidFuzz]
    S --> T[Vendor Risk Aggregation]
    T --> U[(mismatches + duplicates\n+ vendor_analysis tables)]
    U --> V[Update Job Summary\nITC at Risk computed]

    V --> W[[report_task + notify_task]]
    W --> X[Excel Report - OpenPyXL\n5 sheets]
    W --> Y[PDF Report - WeasyPrint]
    X & Y --> Z[(Supabase Storage reports/)]

    W --> AA[Email via Resend]
    W --> BB[Supabase Realtime Push]
    BB --> CC([User: View Dashboard])
    CC --> DD([User: Download Report])
```

---

### 10.2 Mismatch Categories

| Category | Rule | ITC Impact |
|----------|------|------------|
| `MISSING_IN_2B` | Invoice in PR but not in GSTR-2B | **High — ITC denied** |
| `MISSING_IN_PR` | Invoice in 2B but not in PR | Medium — possible omission |
| `GSTIN_MISMATCH` | Supplier GSTIN differs | **High — invalid ITC** |
| `AMOUNT_VARIANCE` | Taxable value variance > ₹1 | High |
| `TAX_RATE_MISMATCH` | Tax rate differs | Medium |
| `DATE_MISMATCH` | Invoice date differs | Low |
| `INVOICE_NUMBER_MISMATCH` | Invoice number format differs | Low |
| `DUPLICATE_INVOICE` | Same invoice appears twice | **High — ITC inflated** |

---

### 10.3 Vendor Risk Scoring

```python
def compute_risk_level(vendor: VendorStats) -> str:
    score = 0
    if vendor.mismatch_rate > 0.30:     score += 3
    elif vendor.mismatch_rate > 0.10:   score += 2
    elif vendor.mismatch_rate > 0.05:   score += 1

    if vendor.itc_at_risk > 100_000:    score += 3
    elif vendor.itc_at_risk > 10_000:   score += 2
    elif vendor.itc_at_risk > 1_000:    score += 1

    if vendor.has_gstin_mismatch:       score += 2
    if vendor.has_duplicates:           score += 2

    if score >= 6:   return "critical"
    elif score >= 4: return "high"
    elif score >= 2: return "medium"
    else:            return "low"
```

---

## Appendix: Tech Decisions Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Job Queue** | ARQ + Redis | Async Python, lightweight, Redis-native |
| **File Parsing** | Pandas + OpenPyXL | Battle-tested for Excel/CSV financial data |
| **PDF Generation** | WeasyPrint + Jinja2 | CSS-styled PDFs, no headless Chrome |
| **Fuzzy Matching** | RapidFuzz | 10x faster than fuzzywuzzy, Levenshtein |
| **Realtime** | Supabase Realtime | WebSocket job status without polling |
| **Email** | Resend | Modern API, React Email templates |
| **Secrets** | Doppler | Centralized, audit-logged secret management |
| **Observability** | Sentry + Grafana | Error tracking + infra metrics |
| **Analytics** | PostHog | Self-hostable product analytics |
| **Region** | Mumbai (bom) | India-first, GST data stays in India |
