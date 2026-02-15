-- Schema derived from services/core-ai/app/models.py
-- Postgres 16+ (uses GENERATED AS IDENTITY)

BEGIN;

-- ---------- Enums ----------
DO $$ BEGIN
  CREATE TYPE ticket_type_enum     AS ENUM ('it', 'facilities', 'hr', 'finance', 'other');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE ticket_status_enum   AS ENUM ('open', 'in_progress', 'resolved', 'closed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE requested_role_enum  AS ENUM ('viewer', 'editor', 'admin', 'owner');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE access_status_enum   AS ENUM ('pending', 'approved', 'rejected');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE event_source_enum    AS ENUM ('leave', 'travel', 'workspace', 'generic');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE resource_type_enum   AS ENUM ('room', 'desk', 'equipment', 'parking');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------- Core domain tables ----------
CREATE TABLE IF NOT EXISTS leaveentitlement (
  id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id         TEXT NOT NULL,
  year            INTEGER NOT NULL,
  leave_type      TEXT NOT NULL,
  days_available  NUMERIC(6,2) NOT NULL,
  month           INTEGER NULL,
  CONSTRAINT uq_leaveentitlement UNIQUE (user_id, year, leave_type, month)
);
CREATE INDEX IF NOT EXISTS ix_leaveentitlement_month ON leaveentitlement (month);

CREATE TABLE IF NOT EXISTS leaverequest (
  id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id         TEXT NOT NULL,
  leave_type      TEXT NOT NULL,
  start_date      DATE NOT NULL,
  end_date        DATE NOT NULL,
  reason          TEXT,
  status          TEXT NOT NULL DEFAULT 'submitted',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  approver_id     TEXT,
  reject_reason   TEXT,
  requested_days  NUMERIC(6,2) NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS expense (
  id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id         TEXT NOT NULL,
  amount          NUMERIC(12,2) NOT NULL,
  currency        CHAR(3) NOT NULL,
  date            DATE NOT NULL,
  category        TEXT NOT NULL,
  project_code    TEXT,
  status          TEXT NOT NULL DEFAULT 'submitted',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS travelrequest (
  id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id         TEXT NOT NULL,
  origin          TEXT NOT NULL,
  destination     TEXT NOT NULL,
  departure_date  DATE NOT NULL,
  return_date     DATE,
  travel_class    TEXT,
  status          TEXT NOT NULL DEFAULT 'submitted',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ticket (
  id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id         TEXT NOT NULL,
  type            ticket_type_enum NOT NULL,
  category        TEXT,
  description     TEXT NOT NULL,
  location        TEXT,
  priority        TEXT,
  status          ticket_status_enum NOT NULL DEFAULT 'open',
  assignee        TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS accessrequest (
  id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id         TEXT NOT NULL,
  resource        TEXT NOT NULL,
  requested_role  requested_role_enum NOT NULL,
  justification   TEXT NOT NULL,
  status          access_status_enum NOT NULL DEFAULT 'pending',
  approver_id     TEXT,
  reject_reason   TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_accessrequest_status ON accessrequest (status);

CREATE TABLE IF NOT EXISTS auditlog (
  id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  actor_id        TEXT NOT NULL,
  action          TEXT NOT NULL,
  target_type     TEXT NOT NULL,
  target_id       TEXT,
  details         JSONB,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_auditlog_actor ON auditlog (actor_id);

CREATE TABLE IF NOT EXISTS calendarevent (
  id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id         TEXT NOT NULL,
  title           TEXT NOT NULL,
  start_time      TIMESTAMPTZ NOT NULL,
  end_time        TIMESTAMPTZ NOT NULL,
  source_type     event_source_enum NOT NULL DEFAULT 'generic',
  source_id       INTEGER,
  status          TEXT NOT NULL DEFAULT 'busy',
  google_event_id TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_calendarevent_source_type ON calendarevent (source_type);
CREATE INDEX IF NOT EXISTS ix_calendarevent_source_id ON calendarevent (source_id);
CREATE INDEX IF NOT EXISTS ix_calendarevent_google_id ON calendarevent (google_event_id);

CREATE TABLE IF NOT EXISTS document (
  id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  owner       TEXT NOT NULL,
  scope       TEXT NOT NULL DEFAULT 'public',
  source      TEXT,
  title       TEXT NOT NULL,
  path        TEXT NOT NULL,
  mime_type   TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS documentchunk (
  id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  document_id INTEGER NOT NULL,
  content     TEXT NOT NULL,
  embedding   BYTEA,
  chunk_index INTEGER NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_documentchunk_document FOREIGN KEY (document_id) REFERENCES document(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_documentchunk_document ON documentchunk (document_id, chunk_index);

-- ---------- Workspace resources ----------
CREATE TABLE IF NOT EXISTS room (
  id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name        TEXT NOT NULL,
  capacity    INTEGER NOT NULL DEFAULT 1,
  location    TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_room_name ON room (name);

CREATE TABLE IF NOT EXISTS desk (
  id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name        TEXT NOT NULL,
  location    TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_desk_name ON desk (name);

CREATE TABLE IF NOT EXISTS equipment (
  id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name        TEXT NOT NULL,
  type        TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_equipment_name ON equipment (name);

CREATE TABLE IF NOT EXISTS parkingspot (
  id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name        TEXT NOT NULL,
  location    TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_parkingspot_name ON parkingspot (name);

CREATE TABLE IF NOT EXISTS booking (
  id             INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id        TEXT NOT NULL,
  resource_type  resource_type_enum NOT NULL,
  resource_id    INTEGER NOT NULL,
  start_time     TIMESTAMPTZ NOT NULL,
  end_time       TIMESTAMPTZ NOT NULL,
  status         TEXT NOT NULL DEFAULT 'confirmed',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_booking_resource_time
  ON booking (resource_type, resource_id, start_time, end_time, status);

-- ---------- Optional local user directory for seed data ----------
CREATE TABLE IF NOT EXISTS app_user (
  id        TEXT PRIMARY KEY,
  email     TEXT NOT NULL,
  full_name TEXT NOT NULL,
  role      TEXT NOT NULL
);

-- ---------- Seed data ----------
INSERT INTO app_user (id, email, full_name, role) VALUES
  ('admin-001', 'admin@example.com', 'Alex Admin', 'system_admin'),
  ('emp-001',   'jane.doe@example.com',  'Jane Doe',  'employee'),
  ('emp-002',   'john.smith@example.com','John Smith','employee'),
  ('emp-003',   'kim.lee@example.com',   'Kim Lee',   'employee')
ON CONFLICT (id) DO NOTHING;

INSERT INTO leaveentitlement (user_id, year, leave_type, days_available, month) VALUES
  -- Default local/dev user (auth disabled)
  ('local-user', 2025, 'sick',   30, NULL),
  ('local-user', 2025, 'annual', 8,  NULL),
  ('local-user', 2026, 'sick',   30, NULL),
  ('local-user', 2026, 'annual', 8,  NULL),
  ('emp-001', 2026, 'annual', 8, NULL),
  ('emp-001', 2026, 'sick',   30, NULL),
  ('emp-001', 2026, 'business', 7, NULL),
  ('emp-001', 2026, 'wedding',   3, NULL),
  ('emp-001', 2026, 'bravement',   5, NULL),
  ('emp-002', 2026, 'annual', 8, NULL),
  ('emp-002', 2026, 'sick',   30, NULL),
  ('emp-002', 2026, 'business', 7, NULL),
  ('emp-002', 2026, 'wedding',   3, NULL),
  ('emp-002', 2026, 'bravement',   5, NULL),
  ('emp-003', 2026, 'annual', 8, NULL),
  ('emp-003', 2026, 'sick',   30, NULL),
  ('emp-003', 2026, 'business', 7, NULL),
  ('emp-003', 2026, 'wedding',   3, NULL),
  ('emp-003', 2026, 'bravement',   5, NULL),
ON CONFLICT DO NOTHING;

INSERT INTO room (name, capacity, location) VALUES
  ('Orion', 10, 'HQ-1F'),
  ('Zephyr', 6, 'HQ-2F')
ON CONFLICT DO NOTHING;

INSERT INTO desk (name, location) VALUES
  ('D-101', 'HQ-1F'),
  ('D-102', 'HQ-1F')
ON CONFLICT DO NOTHING;

INSERT INTO equipment (name, type) VALUES
  ('Laptop-Loaner-1', 'laptop'),
  ('Projector-A', 'projector'),
  ('Pool-Car-1', 'vehicle')
ON CONFLICT DO NOTHING;

INSERT INTO parkingspot (name, location) VALUES
  ('Lot-A-01', 'Basement'),
  ('Lot-A-02', 'Basement')
ON CONFLICT DO NOTHING;

COMMIT;
