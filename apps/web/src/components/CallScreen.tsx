import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AudioTrack,
  LiveKitRoom,
  useRoomContext,
  useTracks,
  useVoiceAssistant,
} from "@livekit/components-react";
import "@livekit/components-styles";
import { Track, ConnectionState, RoomEvent } from "livekit-client";

import { fetchToken, type TokenResponse } from "../lib/api";
import { useAppointments, type Appointment } from "../hooks/useAppointments";
import { useSummary, type CallSummary } from "../hooks/useSummary";
import { normalizeToolEventRow, useToolEvents, type ToolEvent } from "../hooks/useToolEvents";
import { usePreferredAgentTranscriptionSegments } from "../hooks/usePreferredAgentTranscriptionSegments";
import { AppointmentCards } from "./AppointmentCards";
import { AvatarStage } from "./AvatarStage";
import { CallControls } from "./CallControls";
import { ConversationCapturePanel, deriveSnapshot } from "./ConversationCapturePanel";
import { SummaryPanel } from "./SummaryPanel";
import { ToolActivityPanel } from "./ToolActivityPanel";
import { supabase } from "../lib/supabase";

/** Last patient UUID from a successful identify_user in this session (appointments are keyed by user). */
function lastVerifiedUserId(events: ToolEvent[]): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.tool_name !== "identify_user" || e.status !== "succeeded") continue;
    const uid = e.result_summary.user_id;
    if (typeof uid === "string" && uid.length > 0) return uid;
  }
  return null;
}

interface SessionResult {
  sessionId: string;
  duration: number;
  summary: CallSummary | null;
  appointments: Appointment[];
  events: ToolEvent[];
}

function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
}

function PostCallScreen({
  result,
  onRestart,
}: {
  result: SessionResult;
  onRestart: () => void;
}) {
  const successfulEvents = result.events.filter((event) => event.status === "succeeded").length;
  const intake = deriveSnapshot(result.events, result.appointments);
  const intakeComplete =
    intake.phoneNumber !== "Pending" &&
    intake.intent !== "Pending" &&
    (intake.date !== "Pending" || result.events.some((e) => e.tool_name === "cancel_appointment" && e.status === "succeeded"));
  const toolLatencies = result.events
    .filter((event) => event.latency_ms != null && event.status === "succeeded")
    .slice(-12);

  return (
    <div className="call-shell">
      <header className="call-header">
        <div className="clinic-identity">
          <h1>mykare<span>.ai</span></h1>
          <span className="clinic-subtitle">Call complete</span>
        </div>
        <span className="secure-badge">{intakeComplete ? "Handoff ready" : "Partial handoff"}</span>
      </header>

      <div className="post-call-layout">
        <section className="post-call-stage">
          <div className="post-call-copy">
            <p className="post-call-kicker">Conversation finished</p>
            <h2>Clinical handoff — what the doctor should know</h2>
            <p className="post-call-text">
              Below is the same structured intake the receptionist captured live: identity, intent,
              and timing. Use it to prepare for the visit or follow-up without replaying the call.
            </p>
            {!result.summary && (
              <p className="post-call-text doctor-handoff-note">
                No formal wrap-up record was written yet (the patient may have hung up before
                goodbye). Intake fields and tool activity below are still the best source of truth.
              </p>
            )}
          </div>

          <div className="post-call-metrics">
            <div className="post-call-metric">
              <span>Duration</span>
              <strong>{formatDuration(result.duration)}</strong>
            </div>
            <div className="post-call-metric">
              <span>Actions completed</span>
              <strong>{successfulEvents}</strong>
            </div>
            <div className="post-call-metric">
              <span>Appointments touched</span>
              <strong>{result.appointments.length}</strong>
            </div>
          </div>

          <section className="doctor-handoff">
            <h3 className="panel-title">Triage snapshot</h3>
            <ul className="doctor-handoff-list">
              <li>
                <strong>Caller</strong> {intake.name} · {intake.phoneNumber}
              </li>
              <li>
                <strong>Requested</strong> {intake.intent}
              </li>
              <li>
                <strong>When</strong> {intake.date} · {intake.time}
              </li>
              {result.summary?.summary.notes && (
                <li>
                  <strong>Reception notes</strong> {result.summary.summary.notes}
                </li>
              )}
            </ul>
            {toolLatencies.length > 0 && (
              <div className="latency-block">
                <h4 className="panel-title">Recent tool round-trips (ms)</h4>
                <ul className="latency-list">
                  {toolLatencies.map((event) => (
                    <li key={event.id}>
                      <code>{event.tool_name}</code>
                      <span className="latency-ms">{event.latency_ms}ms</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        </section>

        <aside className="call-sidebar">
          <ConversationCapturePanel
            events={result.events}
            appointments={result.appointments}
            variant="review"
          />
          <ToolActivityPanel events={result.events} />
          <AppointmentCards appointments={result.appointments} />
          {result.summary && <SummaryPanel summary={result.summary} />}
        </aside>
      </div>

      <footer className="call-footer">
        <div className="call-status">
          <span className="status-dot status-completed" />
          <span className="status-label">Call ended</span>
          <span className="call-timer">Session {result.sessionId.slice(0, 8)}</span>
        </div>
        <div className="call-buttons">
          <button className="btn-start" onClick={onRestart}>
            Start Another Call
          </button>
        </div>
      </footer>
    </div>
  );
}

function AgentAudioRenderer() {
  const tracks = useTracks([Track.Source.Microphone], {
    onlySubscribed: true,
  }).filter((trackRef) => !trackRef.participant.isLocal && trackRef.publication.kind === Track.Kind.Audio);

  // Bey publishes on behalf of the agent; prefer that track to prevent raw TTS plus avatar audio.
  const preferredTrack =
    tracks.find((trackRef) => Boolean(trackRef.participant.attributes["lk.publish_on_behalf"])) ??
    tracks[0];

  if (!preferredTrack) return null;

  return (
    <div style={{ display: "none" }}>
      <AudioTrack trackRef={preferredTrack} />
    </div>
  );
}

function CallRoom({
  sessionId,
  onAppointmentsChange,
  onSummaryChange,
  onEventsChange,
  onDurationChange,
  onConnectionStateChange,
}: {
  sessionId: string;
  onAppointmentsChange: (appointments: Appointment[]) => void;
  onSummaryChange: (summary: CallSummary | null) => void;
  onEventsChange: (events: ToolEvent[]) => void;
  onDurationChange: (seconds: number) => void;
  onConnectionStateChange: (state: ConnectionState) => void;
}) {
  const room = useRoomContext();
  const { state: agentState, audioTrack, videoTrack } = useVoiceAssistant();
  const agentTranscriptions = usePreferredAgentTranscriptionSegments();
  const [callDuration, setCallDuration] = useState(0);

  // Track actual LiveKit connection state
  useEffect(() => {
    const updateState = (state: ConnectionState) => onConnectionStateChange(state);
    onConnectionStateChange(room.state);
    room.on(RoomEvent.ConnectionStateChanged, updateState);
    room.on(RoomEvent.Connected, () => onConnectionStateChange(ConnectionState.Connected));
    room.on(RoomEvent.Disconnected, () => onConnectionStateChange(ConnectionState.Disconnected));
    room.on(RoomEvent.Reconnecting, () => onConnectionStateChange(ConnectionState.Reconnecting));
    return () => {
      room.off(RoomEvent.ConnectionStateChanged, updateState);
    };
  }, [room, onConnectionStateChange]);

  const toolEvents = useToolEvents(sessionId);
  const callerUserId = useMemo(() => lastVerifiedUserId(toolEvents), [toolEvents]);
  const appointments = useAppointments(callerUserId);
  const summary = useSummary(sessionId);

  useEffect(() => {
    const interval = setInterval(() => setCallDuration((duration) => duration + 1), 1000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    onAppointmentsChange(appointments);
  }, [appointments, onAppointmentsChange]);

  useEffect(() => {
    onSummaryChange(summary);
  }, [summary, onSummaryChange]);

  useEffect(() => {
    onEventsChange(toolEvents);
  }, [toolEvents, onEventsChange]);

  useEffect(() => {
    onDurationChange(callDuration);
  }, [callDuration, onDurationChange]);

  const transcriptions = useMemo(
    () =>
      agentTranscriptions.map((transcription, index) => ({
        id: `${transcription.id || index}`,
        text: transcription.text,
        final: transcription.final,
        receivedAt: transcription.receivedAt,
        startTime: transcription.startTime,
        lastReceivedTime: transcription.lastReceivedTime,
        endTime: transcription.endTime,
      })),
    [agentTranscriptions]
  );

  // Determine UI connection state from actual room + agent state
  const isConnected = room.state === ConnectionState.Connected;
  const isConnecting = room.state === ConnectionState.Connecting ||
    room.state === ConnectionState.Reconnecting;

  return (
    <div className="call-shell">
      <header className="call-header">
        <div className="clinic-identity">
          <h1>mykare<span>.ai</span></h1>
          <span className="clinic-subtitle">Voice Receptionist</span>
        </div>
        <span className="secure-badge">Secure Room</span>
      </header>

      <div className="call-body">
        <div className="call-main">
          <AvatarStage
            videoTrack={videoTrack}
            audioTrack={audioTrack}
            agentState={agentState}
            transcriptions={transcriptions}
          />
          <AgentAudioRenderer />
        </div>

        <aside className="call-sidebar">
          <ConversationCapturePanel events={toolEvents} appointments={appointments} />
          <ToolActivityPanel events={toolEvents} />
          <AppointmentCards appointments={appointments} />
          {summary && <SummaryPanel summary={summary} />}
        </aside>
      </div>

      <footer className="call-footer">
        <CallControls
          isConnected={isConnected}
          isConnecting={isConnecting}
          onStart={() => {}}
          onEnd={() => room.disconnect()}
          agentState={agentState}
          callDuration={callDuration}
        />
        <span className="privacy-notice">Conversation fields are extracted live from the call</span>
      </footer>
    </div>
  );
}

export function CallScreen() {
  const [tokenData, setTokenData] = useState<TokenResponse | null>(null);
  const tokenDataRef = useRef<TokenResponse | null>(null);
  const [isConnecting, setIsConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [completedSession, setCompletedSession] = useState<SessionResult | null>(null);

  useEffect(() => {
    tokenDataRef.current = tokenData;
  }, [tokenData]);

  /** Latest in-call state for disconnect handoff (avoids stale closure on `onDisconnected`). */
  const disconnectSnapshotRef = useRef({
    appointments: [] as Appointment[],
    events: [] as ToolEvent[],
    summary: null as CallSummary | null,
    duration: 0,
  });

  /** Keep handoff ref in sync so `onDisconnected` reads the latest rows without a stale React closure. */
  const handleEventsChange = useCallback((events: ToolEvent[]) => {
    disconnectSnapshotRef.current.events = events;
  }, []);

  const handleAppointmentsChange = useCallback((appointments: Appointment[]) => {
    disconnectSnapshotRef.current.appointments = appointments;
  }, []);

  const handleSummaryChange = useCallback((summary: CallSummary | null) => {
    disconnectSnapshotRef.current.summary = summary;
  }, []);

  const handleDurationChange = useCallback((duration: number) => {
    disconnectSnapshotRef.current.duration = duration;
  }, []);

  const handleConnectionStateChange = useCallback((state: ConnectionState) => {
    // Exposed for debugging — logs the actual room connection lifecycle.
    console.log("[CallScreen] LiveKit connection state:", state);
  }, []);

  const handleStart = useCallback(async () => {
    setError(null);
    setCompletedSession(null);
    disconnectSnapshotRef.current = {
      appointments: [],
      events: [],
      summary: null,
      duration: 0,
    };
    setIsConnecting(true);

    try {
      const data = await fetchToken();
      setTokenData(data);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to connect");
    } finally {
      setIsConnecting(false);
    }
  }, []);

  const handleDisconnected = useCallback(() => {
    const td = tokenDataRef.current;
    if (!td) {
      setTokenData(null);
      return;
    }

    const sessionId = td.session_id;
    void (async () => {
      const snap = disconnectSnapshotRef.current;
      let summary = snap.summary;
      let events = snap.events;
      let appointments = snap.appointments;
      const duration = snap.duration;

      try {
        if (!summary) {
          const { data, error } = await supabase
            .from("call_summaries")
            .select("*")
            .eq("session_id", sessionId)
            .order("created_at", { ascending: false })
            .limit(1)
            .maybeSingle();
          if (!error && data) {
            summary = data as CallSummary;
          }
        }

        const { data: evData, error: evErr } = await supabase
          .from("tool_events")
          .select("*")
          .eq("session_id", sessionId)
          .order("created_at", { ascending: true });
        if (!evErr && evData && evData.length > 0) {
          events = evData.map((row) => normalizeToolEventRow(row as Record<string, unknown>));
        }

        const uid = lastVerifiedUserId(events);
        if (uid) {
          const { data: apptData, error: apErr } = await supabase
            .from("appointments")
            .select("id,user_id,department_id,slot_start_at,slot_end_at,status,reason,departments(name)")
            .eq("user_id", uid)
            .order("slot_start_at", { ascending: false });
          if (!apErr && apptData) {
            appointments = apptData as unknown as Appointment[];
          }
        }
      } catch (e) {
        console.warn("post-call handoff refresh failed", e);
      } finally {
        setCompletedSession({
          sessionId,
          duration,
          summary,
          appointments,
          events,
        });
        setTokenData(null);
      }
    })();
  }, []);

  if (!tokenData && completedSession) {
    return <PostCallScreen result={completedSession} onRestart={handleStart} />;
  }

  if (!tokenData) {
    return (
      <div className="call-shell">
        <header className="call-header">
          <div className="clinic-identity">
            <h1>mykare<span>.ai</span></h1>
            <span className="clinic-subtitle">Voice Receptionist</span>
          </div>
          <span className="secure-badge">Ready</span>
        </header>

        <div className="standby-layout">
          <section className="standby-stage">
            <div className="standby-orb">
              <div className="avatar-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <circle cx="12" cy="8" r="4" />
                  <path d="M4 20c0-4 4-6 8-6s8 2 8 6" />
                </svg>
              </div>
            </div>
            <p className="post-call-kicker">Live call interface</p>
            <h2>Talk to Priya and let the conversation drive the flow.</h2>
            <p className="post-call-text">
              Name, phone number, date, time, and intent will appear only after they are actually
              captured from the call.
            </p>
            {error && <div className="call-error">{error}</div>}
            <button className="btn-start btn-start-large" onClick={handleStart} disabled={isConnecting}>
              {isConnecting ? "Connecting..." : "Start Call"}
            </button>
          </section>

          <aside className="call-sidebar">
            <ConversationCapturePanel events={[]} appointments={[]} />
            <div>
              <h3 className="panel-title">What shows up live</h3>
              <p className="panel-empty">
                As Priya verifies the caller and takes action, the extracted fields and activity
                stream in here.
              </p>
            </div>
          </aside>
        </div>
      </div>
    );
  }

  return (
    <LiveKitRoom
      token={tokenData.token}
      serverUrl={tokenData.server_url}
      connect={true}
      onDisconnected={handleDisconnected}
      audio={true}
    >
      <CallRoom
        sessionId={tokenData.session_id}
        onAppointmentsChange={handleAppointmentsChange}
        onSummaryChange={handleSummaryChange}
        onEventsChange={handleEventsChange}
        onDurationChange={handleDurationChange}
        onConnectionStateChange={handleConnectionStateChange}
      />
    </LiveKitRoom>
  );
}
