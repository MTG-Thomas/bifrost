/**
 * Chat Store - Zustand state management for chat conversations
 *
 * Manages:
 * - Active conversation and agent selection
 * - Messages for conversations (cached locally)
 * - Streaming state for real-time responses
 * - Studio mode for debugging tool calls
 */

import { create } from "zustand";
import type { components } from "@/lib/v1";

import type {
	ToolExecutionState,
	ToolExecutionStatus,
	ToolExecutionLog,
} from "@/components/chat/ToolExecutionCard";
import type { SystemEvent } from "@/components/chat/ChatSystemEvent";
import type { TodoItem } from "@/services/websocket";

// Use generated types from API
type AgentSummary = components["schemas"]["AgentSummary"];
type ConversationSummary = components["schemas"]["ConversationSummary"];
type MessagePublic = components["schemas"]["MessagePublic"];

// Re-export types for external use
export type { ToolExecutionState, ToolExecutionStatus, ToolExecutionLog };


interface ChatState {
	// Active selections
	activeConversationId: string | null;
	activeAgentId: string | null;

	// Cached agents and conversations (for quick display)
	agents: AgentSummary[];
	conversations: ConversationSummary[];

	// Messages per conversation (cached locally)
	messagesByConversation: Record<string, MessagePublic[]>;

	// System events per conversation (agent switches, errors, etc.)
	systemEventsByConversation: Record<string, SystemEvent[]>;

	// Persisted tool executions per conversation (keyed by tool_call_id)
	// This allows us to show execution details after streaming completes
	toolExecutionsByConversation: Record<
		string,
		Record<string, ToolExecutionState>
	>;

	// Streaming state
	isStreaming: boolean;

	// Studio mode (admin feature for debugging)
	isStudioMode: boolean;
	selectedToolCallId: string | null;

	// Connection state
	isConnected: boolean;
	error: string | null;

	// Todo list from agent tools (e.g., TodoWrite tool)
	todos: TodoItem[];

	/** Currently streaming message ID per conversation */
	streamingMessageIds: Record<string, string | null>;
}

interface ChatActions {
	// Selection actions
	setActiveConversation: (conversationId: string | null) => void;
	setActiveAgent: (agentId: string | null) => void;

	// Data actions
	setAgents: (agents: AgentSummary[]) => void;
	setConversations: (conversations: ConversationSummary[]) => void;
	addConversation: (conversation: ConversationSummary) => void;
	removeConversation: (conversationId: string) => void;
	updateConversation: (conversation: ConversationSummary) => void;

	// Message actions
	setMessages: (conversationId: string, messages: MessagePublic[]) => void;
	addMessage: (conversationId: string, message: MessagePublic) => void;
	updateMessage: (
		conversationId: string,
		messageId: string,
		updates: Partial<MessagePublic> & {
			isStreaming?: boolean;
			isFinal?: boolean;
			toolExecutions?: Record<string, ToolExecutionState>;
		},
	) => void;
	clearMessages: (conversationId: string) => void;

	// System event actions
	addSystemEvent: (conversationId: string, event: SystemEvent) => void;
	clearSystemEvents: (conversationId: string) => void;

	// Tool execution persistence actions
	saveToolExecutions: (
		conversationId: string,
		toolExecutions: Record<string, ToolExecutionState>,
	) => void;
	getToolExecution: (
		conversationId: string,
		toolCallId: string,
	) => ToolExecutionState | undefined;

	// Streaming actions
	startStreaming: () => void;
	completeStream: () => void;
	setStreamError: (error: string) => void;
	resetStream: () => void;

	// Studio mode actions
	toggleStudioMode: () => void;
	setStudioMode: (enabled: boolean) => void;
	selectToolCall: (toolCallId: string | null) => void;

	// Connection actions
	setConnected: (connected: boolean) => void;
	setError: (error: string | null) => void;

	// Todo list actions
	setTodos: (todos: TodoItem[]) => void;
	clearTodos: () => void;

	// Streaming message ID per conversation
	setStreamingMessageIdForConversation: (
		conversationId: string,
		messageId: string | null,
	) => void;

	// Reset
	reset: () => void;
}

type ChatStore = ChatState & ChatActions;

const initialState: ChatState = {
	activeConversationId: null,
	activeAgentId: null,
	agents: [],
	conversations: [],
	systemEventsByConversation: {},
	toolExecutionsByConversation: {},
	messagesByConversation: {},
	isStreaming: false,
	isStudioMode: false,
	selectedToolCallId: null,
	isConnected: false,
	error: null,
	todos: [],
	streamingMessageIds: {},
};

export const useChatStore = create<ChatStore>((set, get) => ({
	...initialState,

	// Selection actions
	setActiveConversation: (conversationId) => {
		set({ activeConversationId: conversationId });

		// Reset streaming state when switching conversations
		if (get().isStreaming) {
			set({
				isStreaming: false,
			});
		}
	},

	setActiveAgent: (agentId) => {
		set({ activeAgentId: agentId });
	},

	// Data actions
	setAgents: (agents) => {
		set({ agents });
	},

	setConversations: (conversations) => {
		set({ conversations });
	},

	addConversation: (conversation) => {
		set((state) => ({
			conversations: [conversation, ...state.conversations],
		}));
	},

	removeConversation: (conversationId) => {
		set((state) => {
			const { [conversationId]: _, ...remainingMessages } =
				state.messagesByConversation;
			return {
				conversations: state.conversations.filter(
					(c) => c.id !== conversationId,
				),
				messagesByConversation: remainingMessages,
				// Clear active if this was selected
				activeConversationId:
					state.activeConversationId === conversationId
						? null
						: state.activeConversationId,
			};
		});
	},

	updateConversation: (conversation) => {
		set((state) => ({
			conversations: state.conversations.map((c) =>
				c.id === conversation.id ? conversation : c,
			),
		}));
	},

	// Message actions
	setMessages: (conversationId, messages) => {
		set((state) => ({
			messagesByConversation: {
				...state.messagesByConversation,
				[conversationId]: messages,
			},
		}));
	},

	addMessage: (conversationId, message) => {
		set((state) => ({
			messagesByConversation: {
				...state.messagesByConversation,
				[conversationId]: [
					...(state.messagesByConversation[conversationId] || []),
					message,
				],
			},
		}));
	},

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

	clearMessages: (conversationId) => {
		set((state) => {
			const { [conversationId]: _, ...remaining } =
				state.messagesByConversation;
			return { messagesByConversation: remaining };
		});
	},

	// System event actions
	addSystemEvent: (conversationId, event) => {
		set((state) => ({
			systemEventsByConversation: {
				...state.systemEventsByConversation,
				[conversationId]: [
					...(state.systemEventsByConversation[conversationId] || []),
					event,
				],
			},
		}));
	},

	clearSystemEvents: (conversationId) => {
		set((state) => {
			const { [conversationId]: _, ...remaining } =
				state.systemEventsByConversation;
			return { systemEventsByConversation: remaining };
		});
	},

	// Tool execution persistence actions
	saveToolExecutions: (conversationId, toolExecutions) => {
		set((state) => ({
			toolExecutionsByConversation: {
				...state.toolExecutionsByConversation,
				[conversationId]: {
					...(state.toolExecutionsByConversation[conversationId] ||
						{}),
					...toolExecutions,
				},
			},
		}));
	},

	getToolExecution: (conversationId, toolCallId) => {
		return get().toolExecutionsByConversation[conversationId]?.[toolCallId];
	},

	// Streaming actions
	startStreaming: () => {
		set({ isStreaming: true });
	},

	completeStream: () => {
		set({ isStreaming: false });
	},

	setStreamError: (_error) => {
		set({ isStreaming: false });
	},

	resetStream: () => {
		set({ isStreaming: false });
	},

	// Studio mode actions
	toggleStudioMode: () => {
		set((state) => ({ isStudioMode: !state.isStudioMode }));
	},

	setStudioMode: (enabled) => {
		set({ isStudioMode: enabled });
	},

	selectToolCall: (toolCallId) => {
		set({ selectedToolCallId: toolCallId });
	},

	// Connection actions
	setConnected: (connected) => {
		set({ isConnected: connected });
	},

	setError: (error) => {
		set({ error });
	},

	// Todo list actions
	setTodos: (todos) => {
		set({ todos });
	},

	clearTodos: () => {
		set({ todos: [] });
	},

	// Streaming message ID per conversation
	setStreamingMessageIdForConversation: (conversationId, messageId) => {
		set((state) => ({
			streamingMessageIds: {
				...state.streamingMessageIds,
				[conversationId]: messageId,
			},
		}));
	},

	// Reset
	reset: () => {
		set(initialState);
	},
}));

// Selector hooks for performance
export const useActiveConversation = () =>
	useChatStore((state) => state.activeConversationId);
export const useActiveAgent = () =>
	useChatStore((state) => state.activeAgentId);
export const useIsStreaming = () => useChatStore((state) => state.isStreaming);
export const useIsStudioMode = () =>
	useChatStore((state) => state.isStudioMode);
export const useTodos = () => useChatStore((state) => state.todos);
