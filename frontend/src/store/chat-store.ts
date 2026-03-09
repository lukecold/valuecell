import { create } from "zustand";

/** A single chat message in the strategy chat panel. */
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  proposal?: string;
  originalPrompt?: string;
  proposalStatus?: "pending" | "accepted" | "rejected";
}

interface ChatStore {
  /** Messages keyed by strategy ID (string). Persists until page refresh. */
  messagesByStrategy: Record<string, ChatMessage[]>;
  getMessages: (strategyId: string) => ChatMessage[];
  setMessages: (strategyId: string, messages: ChatMessage[]) => void;
}

export const useChatStore = create<ChatStore>((set, get) => ({
  messagesByStrategy: {},

  getMessages: (strategyId) => get().messagesByStrategy[strategyId] ?? [],

  setMessages: (strategyId, messages) =>
    set((state) => ({
      messagesByStrategy: {
        ...state.messagesByStrategy,
        [strategyId]: messages,
      },
    })),
}));
