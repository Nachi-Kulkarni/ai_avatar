-- Healthcare Voice Agent Schema
-- All timestamps in UTC, display in IST (UTC+5:30)

-- Departments (reference data)
CREATE TABLE IF NOT EXISTS departments (
    id SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Users / Patients
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone TEXT NOT NULL UNIQUE,
    name TEXT,
    patient_number TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Appointment time slots
CREATE TABLE IF NOT EXISTS appointment_slots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    department_id SMALLINT NOT NULL REFERENCES departments(id),
    slot_start_at TIMESTAMPTZ NOT NULL,
    slot_end_at TIMESTAMPTZ NOT NULL,
    is_available BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Appointments
CREATE TABLE IF NOT EXISTS appointments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    department_id SMALLINT NOT NULL REFERENCES departments(id),
    slot_start_at TIMESTAMPTZ NOT NULL,
    slot_end_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked', 'cancelled', 'completed', 'no_show')),
    reason TEXT,
    notes TEXT,
    lock_version INTEGER NOT NULL DEFAULT 1,
    idempotency_key TEXT,
    cancelled_at TIMESTAMPTZ,
    cancellation_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Prevent double booking: one booked appointment per department+slot
CREATE UNIQUE INDEX IF NOT EXISTS idx_appointments_no_double_book
    ON appointments (department_id, slot_start_at)
    WHERE status = 'booked';

-- Prevent same patient from having two booked appointments at the same time
CREATE UNIQUE INDEX IF NOT EXISTS idx_appointments_no_patient_overlap
    ON appointments (user_id, slot_start_at)
    WHERE status = 'booked';

-- Immutable audit trail for appointment lifecycle
CREATE TABLE IF NOT EXISTS appointment_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    appointment_id UUID REFERENCES appointments(id),
    event_type TEXT NOT NULL CHECK (event_type IN ('created', 'modified', 'cancelled', 'completed', 'no_show', 'conflict', 'failure')),
    old_values JSONB,
    new_values JSONB,
    actor TEXT NOT NULL DEFAULT 'agent',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Conversation sessions (one per call)
CREATE TABLE IF NOT EXISTS conversation_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_name TEXT NOT NULL,
    user_id UUID REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'failed')),
    session_state TEXT NOT NULL DEFAULT 'connecting',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ
);

-- Transcript messages
CREATE TABLE IF NOT EXISTS transcript_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES conversation_sessions(id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    text TEXT NOT NULL,
    confidence REAL,
    source TEXT DEFAULT 'stt',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tool call events (visible in UI)
CREATE TABLE IF NOT EXISTS tool_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES conversation_sessions(id),
    appointment_id UUID REFERENCES appointments(id),
    tool_name TEXT NOT NULL,
    input_summary JSONB,
    result_summary JSONB,
    status TEXT NOT NULL DEFAULT 'started' CHECK (status IN ('started', 'in_progress', 'succeeded', 'failed', 'needs_confirmation')),
    latency_ms INTEGER,
    actor TEXT NOT NULL DEFAULT 'agent',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Call summaries
CREATE TABLE IF NOT EXISTS call_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL UNIQUE REFERENCES conversation_sessions(id),
    user_id UUID NOT NULL REFERENCES users(id),
    summary JSONB NOT NULL,
    preferences JSONB,
    appointment_ids UUID[],
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Enable RLS on all tables containing PHI-like data
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE appointments ENABLE ROW LEVEL SECURITY;
ALTER TABLE appointment_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE transcript_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_summaries ENABLE ROW LEVEL SECURITY;

-- Service role can do everything (used by the Python agent)
-- The anon key is used by the browser and restricted by RLS policies

-- Realtime publications for UI updates
ALTER PUBLICATION supabase_realtime ADD TABLE tool_events;
ALTER PUBLICATION supabase_realtime ADD TABLE appointments;
ALTER PUBLICATION supabase_realtime ADD TABLE call_summaries;
ALTER PUBLICATION supabase_realtime ADD TABLE conversation_sessions;

-- Updated_at trigger function
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_appointments_updated BEFORE UPDATE ON appointments
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_tool_events_updated BEFORE UPDATE ON tool_events
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
