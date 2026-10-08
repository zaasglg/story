-- PostgreSQL core schema
CREATE TABLE users (
 id UUID PRIMARY KEY,
 phone VARCHAR(32) UNIQUE NOT NULL,
 role VARCHAR(20) NOT NULL CHECK (role IN ('WORKER','MASTER','ADMIN','BOSS')),
 is_active BOOLEAN NOT NULL DEFAULT TRUE,
 created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE workers (
 id UUID PRIMARY KEY,
 user_id UUID UNIQUE REFERENCES users(id),
 full_name TEXT NOT NULL,
 passport_number TEXT,
 photo_url TEXT,
 city TEXT,
 brigade_id UUID,
 master_id UUID REFERENCES users(id),
 employee_id TEXT UNIQUE,
 hire_date DATE,
 access_status TEXT DEFAULT 'ACTIVE',
 deleted_at TIMESTAMPTZ
);

CREATE TABLE brigades (
 id UUID PRIMARY KEY,
 name TEXT NOT NULL,
 city TEXT,
 master_id UUID REFERENCES users(id)
);

CREATE TABLE objects (
 id UUID PRIMARY KEY,
 name TEXT NOT NULL,
 city TEXT,
 address TEXT,
 status TEXT,
 deleted_at TIMESTAMPTZ
);

CREATE TABLE work_days (
 id UUID PRIMARY KEY,
 worker_id UUID REFERENCES workers(id),
 object_id UUID REFERENCES objects(id),
 brigade_id UUID REFERENCES brigades(id),
 work_date DATE NOT NULL,
 check_in TIMESTAMPTZ,
 check_out TIMESTAMPTZ,
 hours NUMERIC(8,2),
 m3 NUMERIC(12,2),
 rate_per_m3 NUMERIC(12,2),
 daily_earnings NUMERIC(14,2),
 status TEXT NOT NULL,
 created_by UUID REFERENCES users(id),
 updated_by UUID REFERENCES users(id)
);

CREATE TABLE worker_bank_accounts (
 id UUID PRIMARY KEY,
 worker_id UUID REFERENCES workers(id),
 bank_name TEXT NOT NULL,
 iban_encrypted TEXT NOT NULL,
 is_primary BOOLEAN DEFAULT FALSE
);

CREATE TABLE company_bank_accounts (
 id UUID PRIMARY KEY,
 name TEXT NOT NULL,
 iban_encrypted TEXT NOT NULL
);

CREATE TABLE revenues (
 id UUID PRIMARY KEY,
 object_id UUID REFERENCES objects(id),
 city TEXT,
 revenue_date DATE NOT NULL,
 amount NUMERIC(14,2) NOT NULL,
 currency VARCHAR(8) NOT NULL,
 category TEXT,
 comment TEXT,
 created_by UUID REFERENCES users(id),
 updated_by UUID REFERENCES users(id)
);

CREATE TABLE expenses (
 id UUID PRIMARY KEY,
 object_id UUID REFERENCES objects(id),
 city TEXT,
 expense_date DATE NOT NULL,
 amount NUMERIC(14,2) NOT NULL,
 currency VARCHAR(8) NOT NULL,
 category TEXT NOT NULL,
 comment TEXT,
 receipt_url TEXT,
 created_by UUID REFERENCES users(id),
 updated_by UUID REFERENCES users(id),
 deleted_at TIMESTAMPTZ
);

CREATE TABLE financial_requests (
 id UUID PRIMARY KEY,
 type TEXT CHECK (type IN ('SALARY','BONUS','PENALTY','ADVANCE','PAYMENT')),
 worker_id UUID REFERENCES workers(id),
 amount NUMERIC(14,2) NOT NULL,
 reason TEXT,
 status TEXT DEFAULT 'PENDING_BOSS',
 created_by UUID REFERENCES users(id),
 approved_by UUID REFERENCES users(id),
 approved_at TIMESTAMPTZ,
 rejected_by UUID REFERENCES users(id),
 rejected_at TIMESTAMPTZ
);

CREATE TABLE equipment (
 id UUID PRIMARY KEY,
 name TEXT NOT NULL,
 serial_number TEXT,
 quantity INT DEFAULT 0
);

CREATE TABLE equipment_issues (
 id UUID PRIMARY KEY,
 equipment_id UUID REFERENCES equipment(id),
 worker_id UUID REFERENCES workers(id),
 issue_date DATE NOT NULL,
 return_date DATE,
 status TEXT DEFAULT 'ISSUED',
 created_by UUID REFERENCES users(id)
);

CREATE TABLE rating_events (
 id UUID PRIMARY KEY,
 worker_id UUID REFERENCES workers(id),
 event_date DATE NOT NULL,
 event_type TEXT NOT NULL,
 delta NUMERIC(4,2) NOT NULL,
 reason TEXT NOT NULL,
 photo_url TEXT,
 created_by UUID REFERENCES users(id)
);

CREATE TABLE audit_logs (
 id UUID PRIMARY KEY,
 actor_user_id UUID REFERENCES users(id),
 action TEXT NOT NULL,
 entity_type TEXT NOT NULL,
 entity_id UUID,
 old_value JSONB,
 new_value JSONB,
 created_at TIMESTAMPTZ DEFAULT now()
);
