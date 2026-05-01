interface Props {
  isConnected: boolean;
  isConnecting: boolean;
  onStart: () => void;
  onEnd: () => void;
  agentState: string;
  callDuration: number;
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

const STATE_LABELS: Record<string, string> = {
  connecting: "Connecting",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  tool_running: "Processing",
  waiting_for_confirmation: "Confirming",
  completed: "Ended",
  failed: "Failed",
};

export function CallControls({ isConnected, isConnecting, onStart, onEnd, agentState, callDuration }: Props) {
  return (
    <div className="call-controls">
      <div className="call-status">
        {isConnected && (
          <>
            <span className={`status-dot status-${agentState}`} />
            <span className="status-label">{STATE_LABELS[agentState] || agentState}</span>
            <span className="call-timer">{formatDuration(callDuration)}</span>
          </>
        )}
        {isConnecting && <span className="status-label">Connecting...</span>}
      </div>
      <div className="call-buttons">
        {!isConnected && !isConnecting && (
          <button className="btn-start" onClick={onStart}>
            Start Call
          </button>
        )}
        {isConnecting && (
          <button className="btn-start" disabled>
            Connecting...
          </button>
        )}
        {isConnected && (
          <button className="btn-end" onClick={onEnd}>
            End Call
          </button>
        )}
      </div>
    </div>
  );
}
