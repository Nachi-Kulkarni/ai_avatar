import { useEffect, useState } from "react";
import { supabase } from "../lib/supabase";

export interface Appointment {
  id: string;
  user_id?: string;
  department_id: number;
  slot_start_at: string;
  slot_end_at: string;
  status: string;
  reason: string | null;
  departments: { name: string } | null;
}

/**
 * Load and subscribe to appointments for the patient verified in this call (`userId`
 * from `identify_user`). Without a user id we cannot scope rows (appointments have no session_id).
 */
export function useAppointments(userId: string | null) {
  const [appointments, setAppointments] = useState<Appointment[]>([]);

  useEffect(() => {
    if (!userId) {
      setAppointments([]);
      return;
    }

    let cancelled = false;

    void (async () => {
      const { data, error } = await supabase
        .from("appointments")
        .select("id,user_id,department_id,slot_start_at,slot_end_at,status,reason,departments(name)")
        .eq("user_id", userId)
        .order("slot_start_at", { ascending: false });

      if (cancelled || error) return;
      setAppointments(((data || []) as unknown) as Appointment[]);
    })();

    const channel = supabase
      .channel(`appointments-user-${userId}`)
      .on(
        "postgres_changes",
        {
          event: "*",
          schema: "public",
          table: "appointments",
          filter: `user_id=eq.${userId}`,
        },
        (payload) => {
          const appt = payload.new as Appointment;
          if (payload.eventType === "INSERT") {
            setAppointments((prev) => [...prev.filter((a) => a.id !== appt.id), appt]);
          } else if (payload.eventType === "UPDATE") {
            setAppointments((prev) => prev.map((a) => (a.id === appt.id ? appt : a)));
          } else if (payload.eventType === "DELETE") {
            setAppointments((prev) => prev.filter((a) => a.id !== (payload.old as { id: string }).id));
          }
        }
      )
      .subscribe();

    return () => {
      cancelled = true;
      supabase.removeChannel(channel);
    };
  }, [userId]);

  return appointments;
}
