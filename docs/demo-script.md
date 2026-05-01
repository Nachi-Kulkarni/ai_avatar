# Demo Script — Healthcare Voice Receptionist

## Prerequisites

1. All env vars set in `.env` (copy from `.env.example`)
2. Supabase migrations applied (001 + 002)
3. Python agent running: `cd apps/agent && python main.py`
4. Frontend running: `cd apps/web && npm run dev`
5. Open http://localhost:5173

## Golden-Path Demo (5+ exchanges)

### Exchange 1: Greeting
> **Agent**: "Hello! I'm Priya from mykare.ai clinic. How can I help you today?"
> **User**: "Hi, I'd like to book an appointment."

### Exchange 2: User Identification
> **Agent**: "I'd be happy to help you book an appointment. Could I have your name and phone number?"
> **User**: "My name is Rahul Sharma, and my number is 9876543210."
> **Tool**: `identify_user` → visible in Activity panel

### Exchange 3: Department Selection
> **Agent**: "Thank you, Rahul. Which department would you like to visit?"
> **User**: "Cardiology"
> **Tool**: `list_departments` then `fetch_slots` → visible in Activity panel

### Exchange 4: Booking with Confirmation
> **Agent**: "I have a cardiology slot available tomorrow at 10:00 AM. Shall I book that for you?"
> **User**: "Yes, please."
> **Tool**: `record_confirmation` then `book_appointment` → Appointment card appears

### Exchange 5: View Appointments
> **User**: "Can you show me my upcoming appointments?"
> **Tool**: `retrieve_appointments` → Appointment cards update

### Exchange 6: Reschedule
> **User**: "Can I reschedule that to a later time?"
> **Agent**: "I can reschedule your cardiology appointment from 10:00 AM to 2:00 PM tomorrow. Would you like me to confirm that?"
> **User**: "Yes."
> **Tool**: `record_confirmation` then `modify_appointment` → Card updates

### Exchange 7: End Call
> **User**: "That's all, thank you."
> **Tool**: `end_conversation` → Summary panel appears with stats

## Edge Cases to Test

- **Double booking**: Try booking the same slot twice — agent should say "slot unavailable"
- **Invalid phone**: Give a non-Indian number — agent should ask again
- **Cancel flow**: Book then cancel — card should show "cancelled"
- **No slots**: Ask for a department with no availability — agent should suggest alternatives

## Demo Pre-flight (15 min before)

```bash
# Check backend health
curl http://localhost:8000/api/health

# Check Supabase connection
# (verify migrations applied in Supabase dashboard)

# Check LiveKit Cloud status
curl -s https://cloud.livekit.io/api/status
```
