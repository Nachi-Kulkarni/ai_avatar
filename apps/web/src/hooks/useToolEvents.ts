import { useEffect, useState } from "react";
import { supabase } from "../lib/supabase";

export interface ToolEvent {
  id: string;
  tool_name: string;
  status: "started" | "in_progress" | "succeeded" | "failed" | "needs_confirmation";
  input_summary: Record<string, unknown>;
  result_summary: Record<string, unknown>;
  latency_ms: number | null;
  created_at: string;
}

function normalizeSummary(value: unknown): Record<string, unknown> {
  if (!value) return {};
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      return typeof parsed === "object" && parsed !== null ? parsed as Record<string, unknown> : {};
    } catch {
      return {};
    }
  }
  return typeof value === "object" ? value as Record<string, unknown> : {};
}

/** Normalize a `tool_events` row from Supabase or Realtime for UI + handoff. */
export function normalizeToolEventRow(payload: Record<string, unknown>): ToolEvent {
  return {
    ...(payload as unknown as ToolEvent),
    input_summary: normalizeSummary(payload.input_summary),
    result_summary: normalizeSummary(payload.result_summary),
  };
}

export function useToolEvents(sessionId: string | null) {
  const [events, setEvents] = useState<ToolEvent[]>([]);

  useEffect(() => {
    if (!sessionId) return;

    let cancelled = false;

    // Bootstrap: RLS/realtime can miss the first rows if we subscribe after INSERTs.
    void (async () => {
      const { data, error } = await supabase
        .from("tool_events")
        .select("*")
        .eq("session_id", sessionId)
        .order("created_at", { ascending: true });

      if (cancelled || error || !data) return;
      setEvents(data.map((row) => normalizeToolEventRow(row as Record<string, unknown>)));
    })();

    const channel = supabase
      .channel(`tool-events-${sessionId}`)
      .on(
        "postgres_changes",
        {
          event: "INSERT",
          schema: "public",
          table: "tool_events",
          filter: `session_id=eq.${sessionId}`,
        },
        (payload) => {
          setEvents((prev) => [...prev, normalizeToolEventRow(payload.new as Record<string, unknown>)]);
        }
      )
      .on(
        "postgres_changes",
        {
          event: "UPDATE",
          schema: "public",
          table: "tool_events",
          filter: `session_id=eq.${sessionId}`,
        },
        (payload) => {
          setEvents((prev) =>
            prev.map((e) =>
              e.id === payload.new.id ? normalizeToolEventRow(payload.new as Record<string, unknown>) : e
            )
          );
        }
      )
      .subscribe();

    return () => {
      cancelled = true;
      supabase.removeChannel(channel);
    };
  }, [sessionId]);

  return events;
}
