import { VideoTrack, type TrackReference } from "@livekit/components-react";

interface Props {
  videoTrack: TrackReference | undefined;
  audioTrack: TrackReference | undefined;
  agentState: string;
  transcriptions: Array<{
    id: string;
    text: string;
    final: boolean;
    receivedAt: number;
    startTime: number;
    /** Wall clock from livekit-client — bumps on every partial update; best “newest” signal. */
    lastReceivedTime?: number;
    endTime?: number;
  }>;
}

/**
 * Pick the line to show under the avatar: the segment that was **updated** most recently, not
 * necessarily the longest `startTime` in media space (which can keep the first greeting on top).
 */
function pickLatestTranscription(
  transcriptions: Props["transcriptions"],
): Props["transcriptions"][number] | null {
  const nonEmpty = transcriptions
    .map((t, index) => ({ t, index }))
    .filter(({ t }) => t.text.trim().length > 0);
  if (nonEmpty.length === 0) return null;
  nonEmpty.sort((a, b) => {
    const aRecv = a.t.lastReceivedTime ?? a.t.receivedAt ?? 0;
    const bRecv = b.t.lastReceivedTime ?? b.t.receivedAt ?? 0;
    if (bRecv !== aRecv) return bRecv - aRecv;
    const aEnd = a.t.endTime ?? 0;
    const bEnd = b.t.endTime ?? 0;
    if (bEnd !== aEnd) return bEnd - aEnd;
    return b.index - a.index;
  });
  return nonEmpty[0]?.t ?? null;
}

export function AvatarStage({ videoTrack, agentState, transcriptions }: Props) {
  const lastTranscript = pickLatestTranscription(transcriptions);

  const stateClass = agentState === "speaking" ? "avatar-speaking"
    : agentState === "listening" ? "avatar-listening"
    : "avatar-idle";

  return (
    <div className="avatar-stage">
      <div className={`avatar-container ${stateClass}`}>
        {videoTrack ? (
          <VideoTrack
            trackRef={videoTrack}
            autoPlay
            playsInline
            muted
            className="avatar-video"
          />
        ) : (
          <div className="avatar-placeholder">
            <div className="avatar-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <circle cx="12" cy="8" r="4" />
                <path d="M4 20c0-4 4-6 8-6s8 2 8 6" />
              </svg>
            </div>
            <span className="avatar-name">Priya</span>
          </div>
        )}
        <div className="avatar-ring" />
        <div className="sound-wave" />
        <div className="sound-wave" />
        <div className="sound-wave" />
      </div>
      {lastTranscript && (
        <div className="transcript-band">
          <p className="transcript-text">{lastTranscript.text}</p>
        </div>
      )}
    </div>
  );
}
