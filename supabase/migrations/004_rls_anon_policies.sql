-- Fix RLS: ensure all anon SELECT policies exist so the frontend (using anon key) can read data.
-- Migrations 002/003 may not have been applied to the hosted Supabase instance.

-- Drop stale policies if they exist (idempotent)
DROP POLICY IF EXISTS tool_events_anon_select ON public.tool_events;
DROP POLICY IF EXISTS call_summaries_anon_select ON public.call_summaries;
DROP POLICY IF EXISTS conversation_sessions_anon_select ON public.conversation_sessions;
DROP POLICY IF EXISTS appointments_anon_select ON public.appointments;
DROP POLICY IF EXISTS transcript_messages_anon_select ON public.transcript_messages;
DROP POLICY IF EXISTS appointment_events_anon_select ON public.appointment_events;
DROP POLICY IF EXISTS users_anon_select ON public.users;
DROP POLICY IF EXISTS appointment_slots_anon_select ON public.appointment_slots;
DROP POLICY IF EXISTS departments_anon_select ON public.departments;

-- Create anon SELECT policies for all tables used by the frontend
CREATE POLICY tool_events_anon_select ON public.tool_events FOR SELECT TO anon USING (true);
CREATE POLICY call_summaries_anon_select ON public.call_summaries FOR SELECT TO anon USING (true);
CREATE POLICY conversation_sessions_anon_select ON public.conversation_sessions FOR SELECT TO anon USING (true);
CREATE POLICY appointments_anon_select ON public.appointments FOR SELECT TO anon USING (true);
CREATE POLICY transcript_messages_anon_select ON public.transcript_messages FOR SELECT TO anon USING (true);
CREATE POLICY appointment_events_anon_select ON public.appointment_events FOR SELECT TO anon USING (true);
CREATE POLICY users_anon_select ON public.users FOR SELECT TO anon USING (true);
CREATE POLICY appointment_slots_anon_select ON public.appointment_slots FOR SELECT TO anon USING (true);
CREATE POLICY departments_anon_select ON public.departments FOR SELECT TO anon USING (true);

-- Ensure Realtime publications include all frontend-subscribed tables
ALTER PUBLICATION supabase_realtime ADD TABLE tool_events;
ALTER PUBLICATION supabase_realtime ADD TABLE appointments;
ALTER PUBLICATION supabase_realtime ADD TABLE call_summaries;
ALTER PUBLICATION supabase_realtime ADD TABLE conversation_sessions;
