import {
  Bot,
  Check,
  ChevronDown,
  ChevronUp,
  Loader2,
  Send,
  Square,
  User,
  X,
} from "lucide-react";
import { type FC, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  type ChatStreamDoneEvent,
  streamStrategyChat,
  useUpdateStrategyPromptMutation,
} from "@/api/strategy";
import { Button } from "@/components/ui/button";

// ---------------------------------------------------------------------------
// Diff helpers
// ---------------------------------------------------------------------------

interface DiffLine {
  type: "unchanged" | "added" | "removed";
  text: string;
}

/** Line-level LCS diff. Returns an array of diff lines in order. */
function computeLineDiff(original: string, proposed: string): DiffLine[] {
  const a = original.split("\n");
  const b = proposed.split("\n");
  const m = a.length;
  const n = b.length;

  // Build LCS table
  const dp: number[][] = Array.from({ length: m + 1 }, () =>
    new Array(n + 1).fill(0),
  );
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] =
        a[i - 1] === b[j - 1]
          ? dp[i - 1][j - 1] + 1
          : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }

  // Backtrack
  const result: DiffLine[] = [];
  let i = m;
  let j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) {
      result.unshift({ type: "unchanged", text: a[i - 1] });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      result.unshift({ type: "added", text: b[j - 1] });
      j--;
    } else {
      result.unshift({ type: "removed", text: a[i - 1] });
      i--;
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// ProposalDiff component
// ---------------------------------------------------------------------------

const ProposalDiff: FC<{ original: string; proposed: string }> = ({
  original,
  proposed,
}) => {
  const diff = useMemo(
    () =>
      original
        ? computeLineDiff(original, proposed)
        : proposed
            .split("\n")
            .map((text) => ({ type: "added" as const, text })),
    [original, proposed],
  );

  return (
    <div className="max-h-52 overflow-y-auto rounded-md border border-border bg-muted/40 font-mono text-xs leading-5">
      {diff.map((line, idx) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: stable diff output
        <div
          key={idx}
          className={
            line.type === "added"
              ? "bg-green-500/10 text-green-700 dark:text-green-400"
              : line.type === "removed"
                ? "bg-red-500/10 text-red-700 dark:text-red-400"
                : "text-muted-foreground"
          }
        >
          <span className="mr-2 select-none opacity-60">
            {line.type === "added" ? "+" : line.type === "removed" ? "−" : " "}
          </span>
          {/* preserve empty lines */}
          {line.text || "\u00A0"}
        </div>
      ))}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Message {
  role: "user" | "assistant";
  content: string;
  proposal?: string;
  originalPrompt?: string;
  proposalStatus?: "pending" | "accepted" | "rejected";
}

interface StrategyChatPanelProps {
  /** Strategy ID — typed as string|number to match the existing Strategy type  */
  strategyId: string | number;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const StrategyChatPanel: FC<StrategyChatPanelProps> = ({ strategyId }) => {
  const { t } = useTranslation();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [refiningIndex, setRefiningIndex] = useState<number | null>(null);
  const [refineInput, setRefineInput] = useState("");
  const [expandedProposals, setExpandedProposals] = useState<Set<number>>(
    new Set(),
  );
  // Streaming state
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingContent, setStreamingContent] = useState("");

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const { mutateAsync: updatePrompt, isPending: isUpdating } =
    useUpdateStrategyPromptMutation();

  // Scroll to bottom on new messages or streaming content changes
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isStreaming, streamingContent]);

  /** Build flat history for the backend, embedding proposal text into assistant content */
  const buildHistory = (msgs: Message[]) =>
    msgs.map((m) => ({
      role: m.role,
      content: m.proposal
        ? `${m.content}\n\nProposed prompt revision:\n${m.proposal}`
        : m.content,
    }));

  const handleStop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
  };

  const send = async (text: string, baseMessages?: Message[]) => {
    const trimmed = text.trim();
    if (!trimmed || isStreaming) return;

    const base = baseMessages ?? messages;
    const history = buildHistory(base);
    const nextMessages: Message[] = [
      ...base,
      { role: "user", content: trimmed },
    ];
    setMessages(nextMessages);
    setInput("");
    setStreamingContent("");
    setIsStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamStrategyChat(
        {
          strategy_id: String(strategyId),
          message: trimmed,
          history,
        },
        {
          onChunk: (text) => setStreamingContent((prev) => prev + text),
          onDone: (event: ChatStreamDoneEvent) => {
            const assistantMsg: Message = {
              role: "assistant",
              content: event.explanation,
              proposal: event.prompt_proposal,
              originalPrompt: event.original_prompt,
              proposalStatus: event.prompt_proposal ? "pending" : undefined,
            };
            setMessages((prev) => [...prev, assistantMsg]);
            setStreamingContent("");
            // Auto-expand new proposal
            if (event.prompt_proposal) {
              setExpandedProposals((prev) => {
                const next = new Set(prev);
                next.add(nextMessages.length);
                return next;
              });
            }
          },
          onError: (message) => {
            setMessages((prev) => [
              ...prev,
              { role: "assistant", content: message },
            ]);
            setStreamingContent("");
          },
        },
        controller.signal,
      );
    } catch (err) {
      const aborted =
        controller.signal.aborted ||
        (err instanceof DOMException && err.name === "AbortError");
      if (!aborted) {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: t("strategy.chat.error") },
        ]);
      }
      setStreamingContent("");
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }

    inputRef.current?.focus();
  };

  const handleAccept = async (index: number) => {
    const msg = messages[index];
    if (!msg.proposal) return;
    try {
      await updatePrompt({
        strategy_id: String(strategyId),
        prompt_text: msg.proposal,
      });
      setMessages((prev) =>
        prev.map((m, i) =>
          i === index ? { ...m, proposalStatus: "accepted" } : m,
        ),
      );
    } catch {
      // keep pending so user can retry
    }
  };

  const handleReject = (index: number) => {
    setMessages((prev) =>
      prev.map((m, i) =>
        i === index ? { ...m, proposalStatus: "rejected" } : m,
      ),
    );
    if (refiningIndex === index) setRefiningIndex(null);
  };

  const handleSubmitRefinement = (index: number) => {
    const trimmed = refineInput.trim();
    if (!trimmed) return;
    const msg = messages[index];
    const refinementMessage = `Please revise the following prompt proposal based on my feedback.\n\nCurrent proposal:\n${msg.proposal}\n\nFeedback: ${trimmed}`;
    const updated = messages.map((m, i) =>
      i === index ? { ...m, proposalStatus: "rejected" as const } : m,
    );
    setRefiningIndex(null);
    setRefineInput("");
    send(refinementMessage, updated);
  };

  const toggleProposal = (index: number) => {
    setExpandedProposals((prev) => {
      const next = new Set(prev);
      next.has(index) ? next.delete(index) : next.add(index);
      return next;
    });
  };

  const quickPrompts: string[] = [
    t("strategy.chat.quick.explain"),
    t("strategy.chat.quick.failures"),
    t("strategy.chat.quick.improve"),
  ];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Message list */}
      <div className="scroll-container flex-1 p-4">
        {messages.length === 0 && !isStreaming ? (
          <div className="flex h-full flex-col items-center justify-center gap-4 px-4 text-center">
            <div className="flex size-12 items-center justify-center rounded-full bg-muted">
              <Bot className="size-6 text-muted-foreground" />
            </div>
            <p className="max-w-[240px] text-muted-foreground text-sm leading-relaxed">
              {t("strategy.chat.empty")}
            </p>
            <div className="flex w-full flex-col gap-2">
              {quickPrompts.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => send(prompt)}
                  className="rounded-lg border border-border bg-card px-4 py-2 text-left text-foreground text-sm transition-colors hover:bg-muted"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {messages.map((msg, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: stable list
              <div key={i} className="flex flex-col gap-2">
                {/* Bubble */}
                <div
                  className={`flex gap-2 ${msg.role === "user" ? "flex-row-reverse" : ""}`}
                >
                  <div
                    className={`flex size-7 shrink-0 items-center justify-center rounded-full ${
                      msg.role === "user" ? "bg-primary" : "bg-muted"
                    }`}
                  >
                    {msg.role === "user" ? (
                      <User className="size-3.5 text-primary-foreground" />
                    ) : (
                      <Bot className="size-3.5 text-muted-foreground" />
                    )}
                  </div>
                  <div
                    className={`max-w-[82%] rounded-lg px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap ${
                      msg.role === "user"
                        ? "bg-primary text-primary-foreground"
                        : "bg-muted text-foreground"
                    }`}
                  >
                    {msg.content}
                  </div>
                </div>

                {/* Proposal card (not shown when rejected) */}
                {msg.proposal && msg.proposalStatus !== "rejected" && (
                  <div className="ml-9 rounded-lg border border-border bg-card">
                    {/* Collapsible header */}
                    <button
                      type="button"
                      onClick={() => toggleProposal(i)}
                      className="flex w-full items-center justify-between px-3 py-2 text-left"
                    >
                      <span className="font-medium text-foreground text-xs">
                        {msg.proposalStatus === "accepted"
                          ? t("strategy.chat.proposal.accepted")
                          : t("strategy.chat.proposal.title")}
                      </span>
                      <div className="flex items-center gap-1.5">
                        {msg.proposalStatus === "accepted" && (
                          <Check className="size-3.5 text-green-500" />
                        )}
                        {expandedProposals.has(i) ? (
                          <ChevronUp className="size-3.5 text-muted-foreground" />
                        ) : (
                          <ChevronDown className="size-3.5 text-muted-foreground" />
                        )}
                      </div>
                    </button>

                    {expandedProposals.has(i) && (
                      <div className="border-border border-t px-3 pb-3 pt-2">
                        {/* Diff view */}
                        <ProposalDiff
                          original={msg.originalPrompt ?? ""}
                          proposed={msg.proposal}
                        />

                        {msg.proposalStatus === "pending" && (
                          <>
                            <div className="mt-3 flex gap-2">
                              <Button
                                size="sm"
                                className="h-7 gap-1 text-xs"
                                onClick={() => handleAccept(i)}
                                disabled={isUpdating}
                              >
                                {isUpdating ? (
                                  <Loader2 className="size-3 animate-spin" />
                                ) : (
                                  <Check className="size-3" />
                                )}
                                {t("strategy.chat.proposal.accept")}
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-7 text-xs"
                                onClick={() => {
                                  setRefiningIndex(
                                    refiningIndex === i ? null : i,
                                  );
                                  setRefineInput("");
                                }}
                              >
                                {t("strategy.chat.proposal.refine")}
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                className="h-7 gap-1 text-muted-foreground text-xs"
                                onClick={() => handleReject(i)}
                              >
                                <X className="size-3" />
                                {t("strategy.chat.proposal.reject")}
                              </Button>
                            </div>

                            {/* Inline refinement input */}
                            {refiningIndex === i && (
                              <div className="mt-2 flex flex-col gap-2">
                                <textarea
                                  className="w-full rounded-md border border-border bg-muted px-3 py-2 text-foreground text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                                  rows={3}
                                  placeholder={t(
                                    "strategy.chat.proposal.refinePlaceholder",
                                  )}
                                  value={refineInput}
                                  onChange={(e) =>
                                    setRefineInput(e.target.value)
                                  }
                                />
                                <Button
                                  size="sm"
                                  className="h-7 self-end text-xs"
                                  disabled={!refineInput.trim() || isStreaming}
                                  onClick={() => handleSubmitRefinement(i)}
                                >
                                  {t(
                                    "strategy.chat.proposal.submitRefinement",
                                  )}
                                </Button>
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}

            {/* Streaming assistant bubble */}
            {isStreaming && (
              <div className="flex gap-2">
                <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted">
                  <Bot className="size-3.5 text-muted-foreground" />
                </div>
                <div className="max-w-[82%] rounded-lg bg-muted px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap text-foreground">
                  {streamingContent ? (
                    <>
                      {streamingContent}
                      <span className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-foreground/60 align-text-bottom" />
                    </>
                  ) : (
                    <Loader2 className="size-4 animate-spin text-muted-foreground" />
                  )}
                </div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input area */}
      <div className="flex gap-2 border-t p-3">
        <input
          ref={inputRef}
          className="flex-1 rounded-lg bg-muted px-3 py-2 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:ring-1 focus:ring-primary disabled:opacity-50"
          placeholder={t("strategy.chat.placeholder")}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              send(input);
            }
          }}
          disabled={isStreaming}
        />
        {isStreaming ? (
          <Button size="icon" variant="outline" onClick={handleStop}>
            <Square className="size-4" />
          </Button>
        ) : (
          <Button
            size="icon"
            onClick={() => send(input)}
            disabled={!input.trim()}
          >
            <Send className="size-4" />
          </Button>
        )}
      </div>
    </div>
  );
};

export default StrategyChatPanel;
