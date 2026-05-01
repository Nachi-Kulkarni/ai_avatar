-- Demo UI: anon must SELECT appointments for the live sidebar + post-call handoff.
-- Scoped the same as other demo policies (see 002); tighten for production.

DROP POLICY IF EXISTS appointments_anon_select ON public.appointments;

CREATE POLICY appointments_anon_select
  ON public.appointments
  FOR SELECT
  TO anon
  USING (true);
