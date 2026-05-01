import { ParticipantAgentAttributes } from "@livekit/components-core";
import {
  useParticipantTracks,
  useRemoteParticipants,
  useTrackTranscription,
} from "@livekit/components-react";
import { ParticipantKind, Track } from "livekit-client";

/**
 * LiveKit `useVoiceAssistant` attaches STT segments to the **main agent** microphone first, then
 * the worker. TTS for Beyond Presence (and similar) is usually on the worker track (`lk.publish_on_behalf`),
 * so captions can freeze on the opening agent line while real speech streams elsewhere.
 * Prefer the worker mic for transcription when it exists.
 */
export function usePreferredAgentTranscriptionSegments() {
  const remoteParticipants = useRemoteParticipants();
  const agent = remoteParticipants.find(
    (p) =>
      p.kind === ParticipantKind.AGENT &&
      !(ParticipantAgentAttributes.PublishOnBehalf in p.attributes),
  );
  const worker = remoteParticipants.find(
    (p) =>
      p.kind === ParticipantKind.AGENT &&
      p.attributes[ParticipantAgentAttributes.PublishOnBehalf] === agent?.identity,
  );
  const agentTracks = useParticipantTracks(
    [Track.Source.Microphone, Track.Source.Camera],
    agent?.identity,
  );
  const workerTracks = useParticipantTracks(
    [Track.Source.Microphone, Track.Source.Camera],
    worker?.identity,
  );
  const micForTranscription =
    workerTracks.find((t) => t.source === Track.Source.Microphone) ??
    agentTracks.find((t) => t.source === Track.Source.Microphone);

  const { segments } = useTrackTranscription(micForTranscription);
  return segments;
}
