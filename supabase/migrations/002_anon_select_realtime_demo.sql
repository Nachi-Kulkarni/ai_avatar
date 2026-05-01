-- Demo / assignment UI: the browser uses the Supabase anon key with Realtime.
-- RLS was enabled on these tables in 001 but no policies were defined, so anon could
-- not SELECT anything — the Vite app never received tool_events / summaries / sessions.
--
-- Scope: permissive read for anon. The app layer already scopes by session UUID from the
-- LiveKit token response. Do not use these policies in production without auth hardening.

DROP POLICY IF EXISTS tool_events_anon_select ON public.tool_events;
DROP POLICY IF EXISTS call_summaries_anon_select ON public.call_summaries;
DROP POLICY IF EXISTS conversation_sessions_anon_select ON public.conversation_sessions;

CREATE POLICY tool_events_anon_select
  ON public.tool_events
  FOR SELECT
  TO anon
  USING (true);

CREATE POLICY call_summaries_anon_select
  ON public.call_summaries
  FOR SELECT
  TO anon
  USING (true);

CREATE POLICY conversation_sessions_anon_select
  ON public.conversation_sessions
  FOR SELECT
  TO anon
  USING (true);
