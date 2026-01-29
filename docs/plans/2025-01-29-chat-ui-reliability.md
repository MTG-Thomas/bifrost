# Chat UI Reliability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix chat UI issues where tools jump position during streaming and messages sometimes don't appear until another message is typed.

**Architecture:** Replace the split streaming/API message architecture with a unified message model. Messages are created once with stable IDs and updated in place as streaming content arrives. This eliminates the "jump" when streaming messages are cleared and replaced by API messages.

**Tech Stack:** React, Zustand, TypeScript, TanStack Query

---

## Task 1: Add Message Helper Utilities

**Files:**
- Create: `client/src/lib/chat-utils.ts`
- Test: Manual verification (utility functions)

**Step 1: Create the utility file with message helpers**

```typescript
// client/src/lib/chat-utils.ts
/**
 * Chat message utility functions for unified message model
 */

import type { components } from "@/lib/v1";

type MessagePublic = components["schemas"]["MessagePublic"];

/**
 * Extended message type with streaming state flags
 */
export interface UnifiedMessage extends MessagePublic {
  isStreaming?: boolean;
  isOptimistic?: boolean;
  isFinal?: boolean;
}

/**
 * Generate a stable UUID for client-side messages
 */
export function generateMessageId(): string {
  return crypto.randomUUID();
}

/**
 * Merge two messages, preserving content if incoming is empty
 */
export function mergeMessages(
  existing: UnifiedMessage,
  incoming: UnifiedMessage
): UnifiedMessage {
  // Preserve existing content if incoming is empty
  const shouldKeepExistingContent =
    (!incoming.content || incoming.content.trim().length === 0) &&
    existing.content &&
    existing.content.trim().length > 0;

  // Deep merge tool_calls
  const mergedToolCalls = incoming.tool_calls ?? existing.tool_calls;

  return {
    ...existing,
    ...incoming,
    content: shouldKeepExistingContent ? existing.content : incoming.content,
    tool_calls: mergedToolCalls,
    // Preserve earliest createdAt
    created_at:
      new Date(existing.created_at).getTime() <
      new Date(incoming.created_at).getTime()
        ? existing.created_at
        : incoming.created_at,
    // Use latest streaming state
    isStreaming: incoming.isStreaming ?? existing.isStreaming,
    isFinal: incoming.isFinal ?? existing.isFinal,
    isOptimistic: incoming.isOptimistic ?? existing.isOptimistic,
  };
}

/**
 * Integrate incoming messages into existing array
 * - Handles optimistic -> server message replacement
 * - Merges by ID
 * - Maintains stable sort order
 */
export function integrateMessages(
  existing: UnifiedMessage[],
  incoming: UnifiedMessage[]
): UnifiedMessage[] {
  const map = new Map<string, UnifiedMessage>();

  // 1. Load existing by stable ID
  existing.forEach((m) => map.set(m.id, m));

  // 2. Process incoming messages
  incoming.forEach((m) => {
    // Check for optimistic replacement (same content, user role)
    if (!m.isOptimistic && m.role === "user") {
      // Find and remove matching optimistic message
      for (const [key, existingMsg] of map) {
        if (
          existingMsg.isOptimistic &&
          existingMsg.role === "user" &&
          existingMsg.content === m.content
        ) {
          map.delete(key);
          break;
        }
      }
    }

    // 3. Merge with existing by ID
    const existingMsg = map.get(m.id);
    if (existingMsg) {
      map.set(m.id, mergeMessages(existingMsg, m));
    } else {
      map.set(m.id, m);
    }
  });

  // 4. Sort by createdAt + ID for stability
  return Array.from(map.values()).sort((a, b) => {
    const timeDiff =
      new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
    return timeDiff !== 0 ? timeDiff : a.id.localeCompare(b.id);
  });
}
```

**Step 2: Verify the file compiles**

Run: `cd client && npm run tsc`
Expected: No errors related to chat-utils.ts

**Step 3: Commit**

```bash
git add client/src/lib/chat-utils.ts
git commit -m "feat(chat): add message utility functions for unified model"
```

---

## Task 2: Update Chat Store - Unified Message Model

**Files:**
- Modify: `client/src/stores/chatStore.ts`

**Step 1: Add streaming message state tracking per conversation**

Replace the `completedStreamingMessages` and `streamingMessage` state with per-message streaming tracking.

In `chatStore.ts`, update the state interface:

```typescript
// REMOVE these from ChatState:
// completedStreamingMessages: StreamingMessage[];
// streamingMessage: StreamingMessage | null;

// ADD this to ChatState:
/** Currently streaming message ID per conversation */
streamingMessageIds: Record<string, string | null>;
```

**Step 2: Add updateMessage action**

Add to ChatActions interface:

```typescript
updateMessage: (
  conversationId: string,
  messageId: string,
  updates: Partial<MessagePublic> & {
    isStreaming?: boolean;
    isFinal?: boolean;
    toolExecutions?: Record<string, ToolExecutionState>;
  }
) => void;
```

Add to store implementation:

```typescript
updateMessage: (conversationId, messageId, updates) => {
  set((state) => {
    const messages = state.messagesByConversation[conversationId] || [];
    const index = messages.findIndex((m) => m.id === messageId);

    if (index === -1) return {};

    const updatedMessages = [...messages];
    updatedMessages[index] = {
      ...updatedMessages[index],
      ...updates,
    };

    return {
      messagesByConversation: {
        ...state.messagesByConversation,
        [conversationId]: updatedMessages,
      },
    };
  });
},
```

**Step 3: Update initialState**

```typescript
const initialState: ChatState = {
  // ... existing fields ...
  streamingMessageIds: {},
  // REMOVE: completedStreamingMessages: [],
  // REMOVE: streamingMessage: null,
};
```

**Step 4: Add setStreamingMessageId action**

```typescript
setStreamingMessageIdForConversation: (conversationId: string, messageId: string | null) => {
  set((state) => ({
    streamingMessageIds: {
      ...state.streamingMessageIds,
      [conversationId]: messageId,
    },
  }));
},
```

**Step 5: Verify compilation**

Run: `cd client && npm run tsc`
Expected: Errors for removed properties (will fix in next tasks)

**Step 6: Commit (partial - store changes)**

```bash
git add client/src/stores/chatStore.ts
git commit -m "feat(chat): add unified message model to chat store"
```

---

## Task 3: Update useChatStream Hook

**Files:**
- Modify: `client/src/hooks/useChatStream.ts`

**Step 1: Import new utilities**

Add at top:

```typescript
import { generateMessageId, type UnifiedMessage } from "@/lib/chat-utils";
```

**Step 2: Update sendMessage to use unified model**

Replace the current `sendMessage` function:

```typescript
const sendMessage = useCallback(
  async (message: string) => {
    if (!conversationId) {
      toast.error("No conversation selected");
      return;
    }

    // Ensure connected
    if (!webSocketService.isConnected()) {
      await webSocketService.connectToChat(conversationId);
    }

    // Generate stable ID for user message
    const userMessageId = generateMessageId();
    const now = new Date().toISOString();

    // Add optimistic user message with stable ID
    const userMessage: UnifiedMessage = {
      id: userMessageId,
      conversation_id: conversationId,
      role: "user",
      content: message,
      sequence: Date.now(),
      created_at: now,
      isOptimistic: true,
    };
    addMessage(conversationId, userMessage);

    // Generate ID for assistant message (will be replaced by backend ID on message_start)
    const assistantMessageId = generateMessageId();

    // Add placeholder streaming message for assistant
    const assistantMessage: UnifiedMessage = {
      id: assistantMessageId,
      conversation_id: conversationId,
      role: "assistant",
      content: "",
      sequence: Date.now() + 1,
      created_at: now,
      isStreaming: true,
      isOptimistic: true,
    };
    addMessage(conversationId, assistantMessage);

    // Track which message is streaming
    useChatStore.getState().setStreamingMessageIdForConversation(conversationId, assistantMessageId);

    // Start streaming state
    startStreaming();

    // Send the chat message
    const sent = webSocketService.sendChatMessage(conversationId, message);
    if (!sent) {
      try {
        await webSocketService.connectToChat(conversationId);
        webSocketService.sendChatMessage(conversationId, message);
      } catch (error) {
        console.error("[useChatStream] Failed to send message:", error);
        setStreamError("Failed to send message");
        resetStream();
      }
    }
  },
  [conversationId, addMessage, startStreaming, setStreamError, resetStream]
);
```

**Step 3: Update handleChunk to update messages in place**

Update the `delta` case:

```typescript
case "delta":
  if (chunk.content) {
    const convId = currentConversationIdRef.current;
    const streamingId = convId
      ? useChatStore.getState().streamingMessageIds[convId]
      : null;

    if (convId && streamingId) {
      const currentMessages = useChatStore.getState().messagesByConversation[convId] || [];
      const currentMsg = currentMessages.find((m) => m.id === streamingId);

      useChatStore.getState().updateMessage(convId, streamingId, {
        content: (currentMsg?.content || "") + chunk.content,
      });
    }
  }
  break;
```

Update the `message_start` case to replace the optimistic assistant message ID with the real one:

```typescript
case "message_start": {
  const convId = currentConversationIdRef.current;
  if (convId && chunk.assistant_message_id) {
    const currentStreamingId = useChatStore.getState().streamingMessageIds[convId];

    if (currentStreamingId) {
      // Update the message with the real ID from backend
      const messages = useChatStore.getState().messagesByConversation[convId] || [];
      const msgIndex = messages.findIndex((m) => m.id === currentStreamingId);

      if (msgIndex >= 0) {
        const updatedMessages = [...messages];
        updatedMessages[msgIndex] = {
          ...updatedMessages[msgIndex],
          id: chunk.assistant_message_id,
          isOptimistic: false,
        };
        useChatStore.getState().setMessages(convId, updatedMessages);
        useChatStore.getState().setStreamingMessageIdForConversation(convId, chunk.assistant_message_id);
      }
    }
  }

  // Invalidate to fetch user message
  if (convId) {
    queryClient.invalidateQueries({
      queryKey: [
        "get",
        "/api/chat/conversations/{conversation_id}/messages",
        { params: { path: { conversation_id: convId } } },
      ],
    });
  }
  break;
}
```

Update the `done` case:

```typescript
case "done": {
  const convId = currentConversationIdRef.current;
  const streamingId = convId
    ? useChatStore.getState().streamingMessageIds[convId]
    : null;

  if (convId && streamingId) {
    // Mark message as no longer streaming
    useChatStore.getState().updateMessage(convId, streamingId, {
      isStreaming: false,
      isFinal: true,
    });

    // Clear streaming ID
    useChatStore.getState().setStreamingMessageIdForConversation(convId, null);
  }

  completeStream();

  // Refresh messages from API
  if (convId) {
    queryClient.invalidateQueries({
      queryKey: [
        "get",
        "/api/chat/conversations/{conversation_id}/messages",
        { params: { path: { conversation_id: convId } } },
      ],
    });
  }
  break;
}
```

**Step 4: Verify compilation**

Run: `cd client && npm run tsc`
Expected: Should compile (may have warnings about unused imports)

**Step 5: Commit**

```bash
git add client/src/hooks/useChatStream.ts
git commit -m "feat(chat): update useChatStream to use unified message model"
```

---

## Task 4: Update ChatWindow Component

**Files:**
- Modify: `client/src/components/chat/ChatWindow.tsx`

**Step 1: Remove StreamingMessageDisplay usage**

Delete the `StreamingMessageDisplay` component definition (lines ~38-105) - we'll render streaming inline with other messages.

**Step 2: Update message rendering**

Replace the current render logic (timeline.map + completedStreamingMessages.map + streamingMessage) with a single unified render:

```typescript
// In the return JSX, replace the current message rendering with:
{messages.map((msg) => {
  // Skip tool result messages
  if (msg.tool_call_id) return null;

  const isStreaming = msg.isStreaming || msg.id === streamingMessageId;

  return (
    <MessageWithToolCards
      key={msg.id}
      message={msg}
      toolResultMessages={toolResultMessages}
      conversationId={conversationId}
      onToolCallClick={onToolCallClick}
      isStreaming={isStreaming}
    />
  );
})}
```

**Step 3: Update MessageWithToolCards to accept isStreaming prop**

Add to the props interface:

```typescript
interface MessageWithToolCardsProps {
  message: MessagePublic;
  toolResultMessages: Map<string, MessagePublic>;
  conversationId: string;
  onToolCallClick?: (toolCall: ToolCall) => void;
  isStreaming?: boolean;
}
```

And pass it through to ChatMessage:

```typescript
<ChatMessage
  message={message}
  onToolCallClick={onToolCallClick}
  isStreaming={isStreaming && !hasToolCalls}
/>
```

**Step 4: Remove completed streaming messages selectors**

Remove usage of:
- `useCompletedStreamingMessages()`
- `useStreamingMessage()`

Replace with:
```typescript
const streamingMessageId = useChatStore(
  (state) => conversationId ? state.streamingMessageIds[conversationId] : null
);
```

**Step 5: Simplify the messages useMemo**

Replace the complex deduplication with simpler ID-based merge:

```typescript
import { integrateMessages, type UnifiedMessage } from "@/lib/chat-utils";

const messages = useMemo(() => {
  const apiMsgs = (apiMessages || []) as UnifiedMessage[];
  const localMsgs = localMessages as UnifiedMessage[];

  return integrateMessages(apiMsgs, localMsgs);
}, [apiMessages, localMessages]);
```

**Step 6: Remove the separate streaming message render sections**

Delete:
- `{/* Completed Streaming Messages */}` section
- `{/* Current Streaming Message */}` section

**Step 7: Verify compilation**

Run: `cd client && npm run tsc`
Expected: No errors

**Step 8: Commit**

```bash
git add client/src/components/chat/ChatWindow.tsx
git commit -m "feat(chat): unify message rendering in ChatWindow"
```

---

## Task 5: Clean Up Removed State

**Files:**
- Modify: `client/src/stores/chatStore.ts`
- Modify: `client/src/hooks/useChatStream.ts`

**Step 1: Remove deprecated store properties**

In `chatStore.ts`, remove:
- `completedStreamingMessages` from state
- `streamingMessage` from state
- `completeCurrentStreamingMessage` action
- `clearCompletedStreamingMessages` action
- `useCompletedStreamingMessages` selector
- `useStreamingMessage` selector
- Related streaming actions that are no longer needed

**Step 2: Remove deprecated imports in useChatStream**

Remove unused imports from the store.

**Step 3: Verify compilation**

Run: `cd client && npm run tsc`
Expected: No errors

**Step 4: Commit**

```bash
git add client/src/stores/chatStore.ts client/src/hooks/useChatStream.ts
git commit -m "refactor(chat): remove deprecated streaming state"
```

---

## Task 6: Handle Tool Calls in Unified Model

**Files:**
- Modify: `client/src/hooks/useChatStream.ts`
- Modify: `client/src/stores/chatStore.ts`

**Step 1: Store tool executions per message (not globally)**

In store, add to the message update logic:

```typescript
// When tool_call arrives, update the streaming message's tool_calls array
case "tool_call":
  if (chunk.tool_call) {
    const convId = currentConversationIdRef.current;
    const streamingId = convId
      ? useChatStore.getState().streamingMessageIds[convId]
      : null;

    if (convId && streamingId) {
      const messages = useChatStore.getState().messagesByConversation[convId] || [];
      const msg = messages.find((m) => m.id === streamingId);

      if (msg) {
        useChatStore.getState().updateMessage(convId, streamingId, {
          tool_calls: [...(msg.tool_calls || []), chunk.tool_call],
        });
      }
    }
  }
  break;
```

**Step 2: Update tool result handling**

Store tool results in the conversation's tool executions map (keep existing pattern but keyed properly).

**Step 3: Verify tool execution display**

Ensure MessageWithToolCards still receives tool execution state correctly.

**Step 4: Verify compilation**

Run: `cd client && npm run tsc`
Expected: No errors

**Step 5: Commit**

```bash
git add client/src/hooks/useChatStream.ts client/src/stores/chatStore.ts
git commit -m "feat(chat): handle tool calls in unified message model"
```

---

## Task 7: Test End-to-End

**Files:**
- None (manual testing)

**Step 1: Start development stack**

Run: `./debug.sh`

**Step 2: Open chat and send a message**

Expected: User message appears immediately, assistant response streams in place

**Step 3: Trigger a tool call**

Send a message that causes a tool execution.
Expected: Tool badge appears inline with message and stays in place

**Step 4: Send rapid messages**

Send 3-4 messages quickly.
Expected: All messages appear without needing to type another

**Step 5: Refresh page during streaming**

Refresh while assistant is responding.
Expected: Page reloads cleanly, partial content may be lost (expected)

**Step 6: Document any issues**

If issues found, note them for follow-up tasks.

---

## Task 8: Final Cleanup and Lint

**Files:**
- All modified files

**Step 1: Run linter**

Run: `cd client && npm run lint`
Expected: No errors

**Step 2: Run type check**

Run: `cd client && npm run tsc`
Expected: No errors

**Step 3: Final commit**

```bash
git add -A
git commit -m "chore(chat): lint and cleanup after unified message model"
```

---

## Verification Checklist

After implementation:

- [ ] Tools don't jump position during streaming
- [ ] All messages appear immediately (no need to type another)
- [ ] Streaming cursor shows in correct position
- [ ] Tool badges render inline with messages
- [ ] AskUserQuestion card appears when triggered
- [ ] Error states are handled gracefully
- [ ] `npm run tsc` passes with no errors
- [ ] `npm run lint` passes with no errors
