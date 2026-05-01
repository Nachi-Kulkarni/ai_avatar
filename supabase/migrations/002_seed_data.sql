-- Seed departments
INSERT INTO departments (name) VALUES
    ('General Medicine'),
    ('Cardiology'),
    ('Dermatology'),
    ('Orthopedics'),
    ('Pediatrics'),
    ('Dental')
ON CONFLICT (name) DO NOTHING;

-- Seed appointment slots for next 7 days (IST = UTC+5:30)
-- Generates slots from 09:00 to 17:00 IST (03:30 to 11:30 UTC) in 30-min intervals
DO $$
DECLARE
    dept RECORD;
    day_offset INTEGER;
    slot_hour INTEGER;
    slot_start TIMESTAMPTZ;
    slot_end TIMESTAMPTZ;
    ist_offset INTERVAL := INTERVAL '5 hours 30 minutes';
BEGIN
    FOR day_offset IN 0..6 LOOP
        FOR dept IN SELECT id FROM departments WHERE is_active = true LOOP
            FOR slot_hour IN 9..16 LOOP
                slot_start := (CURRENT_DATE + day_offset * INTERVAL '1 day' + slot_hour * INTERVAL '1 hour' - ist_offset);
                slot_end := slot_start + INTERVAL '30 minutes';

                INSERT INTO appointment_slots (department_id, slot_start_at, slot_end_at)
                VALUES (dept.id, slot_start, slot_end)
                ON CONFLICT DO NOTHING;
            END LOOP;
        END LOOP;
    END LOOP;
END;
$$;
