import { Bot, Check, ChevronDown, ChevronUp, Loader2, Send, User, X } from "lucide-react";
import { type FC, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  useStrategyChatMutation,
  useUpdateStrategyPromptMutation,
} from "@/api/strategy";
import { Button } from "@/components/ui/button";

interface Message {
  role: "user" | "assistant";
  content: string;
  proposal?: string;
  proposalStatus?: "pending" | "accepted" | "rejected";
}

interface StrategyChatPanelProps {
  /** Strategy ID — typed as string|number to match the existing Strategy type  */
  strategyId: string | number;
}

const StrategyChatPanel: FC<StrategyChatPanelProps> = ({ strategyId }) => {
  const { t } = useTranslation();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [refiningIndex, setRefiningIndex] = useState<number | null>(null);
  const [refineInput, setRefineInput] = useState("");
  const [expandedProposals, setExpandedProposals] = useState<Set<number>>(
    new Set(),
  );
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const { mutateAsync: sendChat, isPending } = useStrategyChatMutation();
  const { mutateAsync: updatePrompt, isPending: isUpdating } =
    useUpdateStrategyPromptMutation();

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isPending]);

  /** Build flat history for the backend, embedding proposal text into assistant content */
  const buildHistory = (msgs: Message[]) =>
    msgs.map((m) => ({
      role: m.role,
      content: m.proposal
        ? `${m.content}\n\nProposed prompt revision:\n${m.proposal}`
        : m.content,
    }));

  const send = async (text: string, baseMessages?: Message[]) => {
    const trimmed = text.trim();
    if (!trimmed || isPending) return;

    const base = baseMessages ?? messages;
    const history = buildHistory(base);
    const nextMessages: Message[] = [
      ...base,
      { role: "user", content: trimmed },
    ];
    setMessages(nextMessages);
    setInput("");

    try {
      const res = await sendChat({
        strategy_id: String(strategyId),
        message: trimmed,
        history,
      });
      const { explanation, prompt_proposal } = res.data;
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: explanation,
          proposal: prompt_proposal,
          proposalStatus: prompt_proposal ? "pending" : undefined,
        },
      ]);
      // Auto-expand new proposal
      if (prompt_proposal) {
        setExpandedProposals((prev) => {
          const next = new Set(prev);
          next.add(nextMessages.length); // index of the new assistant message
          return next;
        });
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: t("strategy.chat.error") },
      ]);
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
    // Mark old proposal as rejected before sending the follow-up
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
        {messages.length === 0 ? (
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
                        {/* Proposal text */}
                        <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap text-muted-foreground text-xs leading-relaxed">
                          {msg.proposal}
                        </pre>

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
                                  disabled={!refineInput.trim() || isPending}
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

            {isPending && (
              <div className="flex gap-2">
                <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted">
                  <Bot className="size-3.5 text-muted-foreground" />
                </div>
                <div className="rounded-lg bg-muted px-3 py-2">
                  <Loader2 className="size-4 animate-spin text-muted-foreground" />
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
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(input);
            }
          }}
          disabled={isPending}
        />
        <Button
          size="icon"
          onClick={() => send(input)}
          disabled={!input.trim() || isPending}
        >
          {isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Send className="size-4" />
          )}
        </Button>
      </div>
    </div>
  );
};

export default StrategyChatPanel;
