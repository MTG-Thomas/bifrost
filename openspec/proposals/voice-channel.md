# Twilio Voice Channel Implementation Plan

**Status**: Proposed
**Created**: 2026-01-03

## Overview

Add Twilio ConversationRelay as a voice channel for Bifrost agents. Uses a multi-tenant model where platform and orgs can have their own Twilio accounts and phone numbers, with agent routing scoped per-number.

## Architecture

```
Caller dials +1-555-0001
           ↓
Twilio webhook: POST /api/voice/incoming (includes To number)
           ↓
Lookup VoiceNumber by phone_number → get agent pool, org context
           ↓
Return TwiML with ConversationRelay WebSocket URL
           ↓
Twilio connects: WebSocket /api/voice/relay/{voice_number_id}
           ↓
VoiceHandler creates Conversation (channel=voice, org_id from number)
           ↓
If routing=fixed: use default_agent
If routing=dynamic: route first utterance among number's agent pool
           ↓
AgentExecutor.chat() handles conversation, tool calls, workflows
           ↓
Stream tokens → Twilio TTS → Caller hears response
```

## Multi-Tenant Model

### Account Hierarchy
```
Platform Level:
  └─ TwilioAccount (scope=platform)
       └─ VoiceNumber: +1-555-0001 (platform line)
       └─ VoiceNumber: +1-555-0002 (serves Org A)

Organization Level (BYOT - Bring Your Own Twilio):
  └─ TwilioAccount (scope=org, org_id=Acme)
       └─ VoiceNumber: +1-555-8888 (Acme's own)
       └─ VoiceNumber: +1-555-8889 (Acme's second line)
```

### Agent Routing Per Number

Unlike chat (which routes based on user roles), voice numbers have no authenticated user. Instead, each number has an **explicit agent pool**:

```python
VoiceNumber:
  routing_mode: "fixed" | "dynamic"
  default_agent_id: UUID  # fallback or sole agent
  agents: [Agent, Agent, ...]  # pool for dynamic routing
```

- **Fixed**: Always routes to `default_agent`
- **Dynamic**: AI routes among `agents` pool based on caller's first message

Example:
```
+1-555-0000 (Main Line):
  routing: dynamic
  agents: [Sales Bot, Support Bot, Billing Bot]
  default_agent: General Assistant (fallback)

Caller: "I need to update my payment method"
→ Routes to Billing Bot
```

## Data Models

### TwilioAccount
```python
class TwilioAccount(Base):
    __tablename__ = "twilio_accounts"

    id: UUID
    scope: str  # "platform" | "organization"
    organization_id: UUID | None  # null if platform-level
    name: str  # "Platform Twilio", "Acme Corp Twilio"
    account_sid: str  # encrypted
    auth_token: str  # encrypted
    base_url: str | None  # Optional override for webhooks
    is_active: bool
    created_at: datetime
    updated_at: datetime
```

### VoiceNumber
```python
class VoiceNumber(Base):
    __tablename__ = "voice_numbers"

    id: UUID
    twilio_account_id: UUID  # FK → TwilioAccount
    organization_id: UUID | None  # which org this serves

    # Twilio identifiers
    phone_number: str  # E.164: "+15550001"
    phone_sid: str  # Twilio's PN SID for API calls

    # Display
    name: str  # "Sales Line", "Main Support"

    # Routing
    routing_mode: str  # "dynamic" | "fixed"
    default_agent_id: UUID | None  # FK → Agent

    # Voice configuration
    greeting: str | None  # Welcome message
    transfer_number: str | None  # For call transfers
    tts_provider: str | None  # "ElevenLabs" | "Google"
    tts_voice_id: str | None
    language: str  # "en-US"

    is_active: bool
    created_at: datetime
    updated_at: datetime

    # Relationships
    agents: list[Agent]  # via voice_number_agents junction
```

### VoiceNumberAgent (Junction)
```python
class VoiceNumberAgent(Base):
    __tablename__ = "voice_number_agents"

    voice_number_id: UUID  # FK
    agent_id: UUID  # FK
    created_at: datetime
```

## API Endpoints

### Twilio Account Management
```
POST   /api/voice/accounts              # Add Twilio account
GET    /api/voice/accounts              # List accounts (scoped by user access)
GET    /api/voice/accounts/{id}         # Get account details
PATCH  /api/voice/accounts/{id}         # Update credentials
DELETE /api/voice/accounts/{id}         # Remove account
```

### Phone Number Management
```
GET    /api/voice/numbers/available     # Search purchasable numbers (Twilio SDK)
POST   /api/voice/numbers               # Purchase + configure number
GET    /api/voice/numbers               # List configured numbers
GET    /api/voice/numbers/{id}          # Get number details
PATCH  /api/voice/numbers/{id}          # Update config (agents, greeting, etc.)
DELETE /api/voice/numbers/{id}          # Release number back to Twilio
```

### Twilio Webhooks (internal)
```
POST   /api/voice/incoming              # Twilio calls this when number rings
POST   /api/voice/status                # Call status updates
POST   /api/voice/action/{conv_id}      # Handoff callback after transfer
WS     /api/voice/relay/{number_id}     # ConversationRelay WebSocket
```

## Twilio SDK Integration

### Number Provisioning Flow
```python
# 1. Search available numbers
available = client.available_phone_numbers("US").local.list(
    area_code="512",
    voice_enabled=True
)

# 2. Purchase and configure
purchased = client.incoming_phone_numbers.create(
    phone_number="+15125551234",
    voice_url=f"https://{host}/api/voice/incoming",
    voice_method="POST",
    status_callback=f"https://{host}/api/voice/status"
)

# 3. Store phone_sid for future API calls
voice_number.phone_sid = purchased.sid
```

### Webhook Auto-Configuration
When number is purchased, SDK automatically sets:
- `voice_url` → `{base_url}/api/voice/incoming`
- `voice_method` → `POST`
- `status_callback` → `{base_url}/api/voice/status`

One endpoint handles all numbers - lookup by `To` parameter.

### Callback URL Management

**Pattern**: Same as Event Sources - API returns paths, frontend combines with `window.location.origin`.

**Base URL Resolution** (for Twilio SDK configuration):
```python
# Priority order:
base_url = (
    twilio_account.base_url  # Per-account override
    or settings.public_base_url  # System default (new setting)
    or "http://localhost:8000"  # Fallback for dev
)
```

**Config Addition** (`api/src/config.py`):
```python
public_base_url: str = Field(
    default="http://localhost:8000",
    description="Public URL for webhooks and callbacks (e.g., https://app.bifrost.com)"
)
```

**TwilioAccount Model** addition:
```python
base_url: str | None  # Optional override for this account's webhooks
```

**Response Model**:
```python
class VoiceNumberResponse(BaseModel):
    # Paths (UI combines with window.location.origin)
    webhook_path: str  # "/api/voice/incoming"
    websocket_path: str  # "/api/voice/relay/{id}"

    # Actual configured URL in Twilio (for verification)
    configured_webhook_url: str  # "https://app.bifrost.com/api/voice/incoming"
```

This allows:
- Frontend to display URL using `window.location.origin + webhook_path`
- Different base URLs per account (ngrok for dev, production for live)
- Verification that Twilio is configured correctly

## ConversationRelay Protocol

### Incoming Call TwiML Response
```xml
<Response>
  <Connect>
    <ConversationRelay
      url="wss://{host}/api/voice/relay/{voice_number_id}"
      ttsProvider="{voice_number.tts_provider}"
      voice="{voice_number.tts_voice_id}"
      language="{voice_number.language}"
      welcomeGreeting="{voice_number.greeting}"
      action="https://{host}/api/voice/action/{conversation_id}" />
  </Connect>
</Response>
```

### WebSocket Messages FROM Twilio
| Type | Description |
|------|-------------|
| `setup` | Initial connection with `callSid` |
| `prompt` | User speech transcription (`voicePrompt`) |
| `interrupt` | User interrupted TTS playback |
| `dtmf` | Keypad press |

### WebSocket Messages TO Twilio
| Type | Description |
|------|-------------|
| `text` | Stream tokens for TTS: `{"type": "text", "token": "...", "last": false}` |
| `play` | Play audio file URL |
| `sendDigits` | Send DTMF tones |
| `language` | Switch TTS/STT language |
| `end` | End session with handoff data |

## Voice Handler Service

```python
class VoiceHandler:
    async def handle_relay(
        self,
        websocket: WebSocket,
        voice_number: VoiceNumber,
    ):
        call_sid = None
        conversation = None
        agent = None

        while websocket.state == OPEN:
            msg = await websocket.receive_json()

            if msg["type"] == "setup":
                call_sid = msg["callSid"]
                # Create conversation
                conversation = await self._create_conversation(
                    voice_number=voice_number,
                    call_sid=call_sid,
                    from_number=msg.get("from"),
                )
                # Set initial agent if fixed routing
                if voice_number.routing_mode == "fixed":
                    agent = voice_number.default_agent

            elif msg["type"] == "prompt":
                user_text = msg["voicePrompt"]

                # Dynamic routing on first message
                if agent is None:
                    agent = await self._route_to_agent(
                        voice_number.agents,
                        user_text,
                        voice_number.default_agent,
                    )

                # Process through agent executor
                async for chunk in self.executor.chat(
                    agent=agent,
                    conversation=conversation,
                    user_message=user_text,
                    stream=True,
                ):
                    await self._handle_chunk(websocket, chunk)

            elif msg["type"] == "interrupt":
                # User interrupted - log for analytics
                pass
```

## Voice-Specific System Tools

Add to agent tool registry:

### transfer_call
```python
{
    "name": "transfer_call",
    "description": "Transfer the caller to a human agent or another phone number",
    "parameters": {
        "reason": "string - why the transfer is happening",
        "summary": "string - conversation summary for the recipient"
    }
}
```
VoiceHandler intercepts this result and sends `{"type": "end", "handoffData": {...}}`

### play_audio
```python
{
    "name": "play_audio",
    "description": "Play an audio file to the caller (hold music, announcements)",
    "parameters": {
        "url": "string - URL of audio file",
        "loop": "integer - number of times to repeat (default 1)"
    }
}
```

### end_call
```python
{
    "name": "end_call",
    "description": "Politely end the call",
    "parameters": {
        "farewell": "string - final message before hanging up"
    }
}
```

## Call Transfer Flow

1. Caller: "Can I speak to a human?"
2. Agent calls `transfer_call` tool with summary
3. VoiceHandler sends to Twilio:
   ```json
   {"type": "end", "handoffData": {"reason": "customer_request", "summary": "..."}}
   ```
4. Twilio closes WebSocket, POSTs to action URL
5. Action endpoint returns TwiML:
   ```xml
   <Response>
     <Say>Transferring you now...</Say>
     <Dial>{voice_number.transfer_number}</Dial>
   </Response>
   ```

## Files to Create

### New Files
```
api/src/models/orm/voice.py           # TwilioAccount, VoiceNumber, VoiceNumberAgent
api/src/models/contracts/voice.py     # Pydantic DTOs
api/src/routers/voice.py              # All voice endpoints
api/src/services/voice_handler.py     # WebSocket handler
api/src/services/voice_router.py      # Agent routing for voice
api/src/services/voice_tools.py       # transfer_call, play_audio, end_call
api/src/services/twilio_service.py    # SDK wrapper for provisioning

api/alembic/versions/xxx_add_voice_tables.py  # Migration

api/tests/unit/services/test_voice_handler.py
api/tests/unit/services/test_voice_router.py
api/tests/integration/api/test_voice.py
```

### Modified Files
```
api/src/main.py                       # Register voice router
api/src/models/orm/__init__.py        # Export new models
api/src/routers/tools.py              # Add voice tools to registry
api/src/config.py                     # Add public_base_url setting
```

## Design Decisions (Confirmed)

1. **Multi-tenant**: Platform and orgs can have own Twilio accounts + numbers
2. **Agent pools**: Each number has assigned agents for routing scope
3. **Inbound only** for v1 - caller dials in
4. **Dial transfer** - transfers go to configured fallback number
5. **TTS configurable** per number (ElevenLabs or Google)
6. **SDK provisioning** - purchase and configure numbers via API
7. **Callback URLs** - API returns paths, frontend uses `window.location.origin`

## Not In Scope (v1)

- Outbound calling (dial out)
- Call recording/storage
- Multi-party calls / conferencing
- IVR menu builder
- Twilio Flex integration
- SMS channel
- Voicemail

## Success Criteria

1. Add platform Twilio account with credentials
2. Search and purchase phone number via API
3. Assign agents to number, configure greeting
4. Call the number, hear greeting, speak to agent
5. Agent can call workflow tools with "thinking" messages
6. Say "transfer me" and get connected to fallback number
7. Org can bring their own Twilio account and numbers
