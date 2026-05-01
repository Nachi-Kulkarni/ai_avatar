import { useEffect, useState } from "react";
import { supabase } from "../lib/supabase";

export interface CallSummary {
  id: string;
  summary: {
    notes: string;
    total_appointments: number;
    booked: Array<{ id: string; status: string; slot_start_at: string; departments: { name: string } }>;
    cancelled: Array<{ id: string; status: string }>;
    timestamp: string;
  };
  created_at: string;
}

export function useSummary(sessionId: string | null) {
  const [summary, setSummary] = useState<CallSummary | null>(null);

  useEffect(() => {
    if (!sessionId) return;

    let cancelled = false;

    void (async () => {
      const { data, error } = await supabase
        .from("call_summaries")
        .select("*")
        .eq("session_id", sessionId)
        .order("created_at", { ascending: false })
        .limit(1)
        .maybeSingle();

      if (cancelled || error || !data) return;
      setSummary(data as CallSummary);
    })();

    const channel = supabase
      .channel(`summary-${sessionId}`)
      .on(
        "postgres_changes",
        {
          event: "INSERT",
          schema: "public",
          table: "call_summaries",
          filter: `session_id=eq.${sessionId}`,
        },
        (payload) => {
          setSummary(payload.new as CallSummary);
        }
      )
      .subscribe();

    return () => {
      cancelled = true;
      supabase.removeChannel(channel);
    };
  }, [sessionId]);

  return summary;
}
