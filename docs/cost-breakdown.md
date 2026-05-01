# Cost Breakdown — Healthcare Voice Agent MVP

Estimated cost per 5-minute demo call.

| Service | Usage per call | Unit Cost | Cost per Call |
|---------|---------------|-----------|---------------|
| LiveKit Cloud | ~5 min room | $0.003/min | $0.015 |
| Deepgram STT | ~3 min audio | $0.0043/min | $0.013 |
| OpenRouter (GPT-4o) | ~500 tokens in, 800 out | $2.50/1M in, $10/1M out | $0.019 |
| Cartesia TTS | ~2 min audio | $0.015/1K chars | $0.045 |
| bey Avatar | ~5 min video | $0.10/min | $0.50 |
| Supabase | Inserts + realtime | Free tier | $0.00 |
| **Total per call** | | | **~$0.59** |

## Notes

- Without bey avatar (voice-only): ~$0.09 per call
- Free tier credits on most platforms cover 50-100+ demo calls
- OpenRouter model can be downgraded to `openai/gpt-4o-mini` for ~80% cost reduction
- Deepgram has a generous free tier (100K min/month)
