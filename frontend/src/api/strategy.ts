import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { API_QUERY_KEYS } from "@/constants/api";
import { type ApiResponse, apiClient, getServerUrl } from "@/lib/api-client";
import type {
  CreateStrategy,
  PortfolioSummary,
  Position,
  PromptVersion,
  Strategy,
  StrategyCompose,
  StrategyPerformance,
  StrategyPrompt,
} from "@/types/strategy";

export const useGetStrategyList = () => {
  return useQuery({
    queryKey: API_QUERY_KEYS.STRATEGY.strategyList,
    queryFn: () =>
      apiClient.get<
        ApiResponse<{
          strategies: Strategy[];
        }>
      >("/strategies/"),
    select: (data) => data.data.strategies,
    refetchInterval: 5 * 1000,
  });
};

export const useGetStrategyDetails = (strategyId?: number) => {
  return useQuery({
    queryKey: API_QUERY_KEYS.STRATEGY.strategyTrades([strategyId ?? ""]),
    queryFn: () =>
      apiClient.get<ApiResponse<StrategyCompose[]>>(
        `/strategies/detail?id=${strategyId}`,
      ),
    select: (data) => data.data,
    refetchInterval: 5 * 1000,
    enabled: !!strategyId,
  });
};

export const useGetStrategyHoldings = (strategyId?: number) => {
  return useQuery({
    queryKey: API_QUERY_KEYS.STRATEGY.strategyHoldings([strategyId ?? ""]),
    queryFn: () =>
      apiClient.get<ApiResponse<Position[]>>(
        `/strategies/holding?id=${strategyId}`,
      ),
    select: (data) => data.data,
    refetchInterval: 5 * 1000,
    enabled: !!strategyId,
  });
};

export const useGetStrategyPriceCurve = (strategyId?: number) => {
  return useQuery({
    queryKey: API_QUERY_KEYS.STRATEGY.strategyPriceCurve([strategyId ?? ""]),
    queryFn: () =>
      apiClient.get<ApiResponse<Array<Array<string | number>>>>(
        `/strategies/holding_price_curve?id=${strategyId}`,
      ),
    select: (data) => data.data,
    refetchInterval: 5 * 1000,
    enabled: !!strategyId,
  });
};

export const useGetStrategyPortfolioSummary = (strategyId?: number) => {
  return useQuery({
    queryKey: API_QUERY_KEYS.STRATEGY.strategyPortfolioSummary([
      strategyId ?? "",
    ]),
    queryFn: () =>
      apiClient.get<ApiResponse<PortfolioSummary>>(
        `/strategies/portfolio_summary?id=${strategyId}`,
      ),
    select: (data) => data.data,
    refetchInterval: 5 * 1000,
    enabled: !!strategyId,
  });
};

export const useCreateStrategy = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: CreateStrategy) =>
      apiClient.post<ApiResponse<{ strategy_id: string }>>(
        "/strategies/create",
        data,
      ),
    onSuccess: () => {
      // Invalidate strategy list to refetch
      queryClient.invalidateQueries({
        queryKey: API_QUERY_KEYS.STRATEGY.strategyList,
      });
    },
  });
};

export const useTestConnection = () => {
  return useMutation({
    mutationFn: (data: CreateStrategy["exchange_config"]) =>
      apiClient.post<ApiResponse<null>>("/strategies/test-connection", data),
  });
};

export const useStopStrategy = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (strategyId: number) =>
      apiClient.post<ApiResponse<{ message: string }>>(
        `/strategies/stop?id=${strategyId}`,
      ),
    onSuccess: () => {
      // Invalidate strategy list to refetch
      queryClient.invalidateQueries({
        queryKey: API_QUERY_KEYS.STRATEGY.strategyList,
      });
    },
  });
};

export const useDeleteStrategy = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (strategyId: number) =>
      apiClient.delete<ApiResponse<null>>(
        `/strategies/delete?id=${strategyId}`,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: API_QUERY_KEYS.STRATEGY.strategyList,
      });
    },
  });
};

export const useRestartStrategy = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (strategyId: number) =>
      apiClient.post<ApiResponse<{ strategy_id: string }>>(
        `/strategies/restart?id=${strategyId}`,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: API_QUERY_KEYS.STRATEGY.strategyList,
      });
    },
  });
};

export const useGetStrategyPrompts = () => {
  return useQuery({
    queryKey: API_QUERY_KEYS.STRATEGY.strategyPrompts,
    queryFn: () =>
      apiClient.get<ApiResponse<StrategyPrompt[]>>("/strategies/prompts/"),
    select: (data) => data.data,
    staleTime: 0,
  });
};

export const useCreateStrategyPrompt = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: Pick<StrategyPrompt, "name" | "content">) =>
      apiClient.post<ApiResponse<StrategyPrompt>>(
        "/strategies/prompts/create",
        data,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: API_QUERY_KEYS.STRATEGY.strategyPrompts,
      });
    },
  });
};

export const useDeleteStrategyPrompt = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (promptId: string) =>
      apiClient.delete<
        ApiResponse<{ deleted: boolean; prompt_id: string; message: string }>
      >(`/strategies/prompts/${promptId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: API_QUERY_KEYS.STRATEGY.strategyPrompts,
      });
    },
  });
};

// ---------------------------------------------------------------------------
// Streaming chat
// ---------------------------------------------------------------------------

export interface ChatStreamDoneEvent {
  type: "done";
  strategy_id: string;
  explanation: string;
  prompt_proposal?: string;
  original_prompt?: string;
}

export interface ChatStreamHandlers {
  onChunk: (text: string) => void;
  onDone: (event: ChatStreamDoneEvent) => void;
  onError: (message: string) => void;
}

/** POST /strategies/chat and consume the SSE stream. */
export async function streamStrategyChat(
  params: {
    strategy_id: string;
    message: string;
    history?: { role: string; content: string }[];
  },
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const url = getServerUrl("/strategies/chat");
  console.debug("[StreamChat] connecting to", url);

  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
    signal,
  });

  if (!response.ok || !response.body) {
    console.error(
      "[StreamChat] bad response:",
      response.status,
      response.statusText,
    );
    handlers.onError(`HTTP ${response.status}: ${response.statusText}`);
    return;
  }

  console.debug("[StreamChat] response OK, reading stream…");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let bytesReceived = 0;

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      console.debug(
        "[StreamChat] stream closed, bytes received:",
        bytesReceived,
      );
      break;
    }

    bytesReceived += value?.length ?? 0;
    buffer += decoder.decode(value, { stream: true });

    // Split on SSE event boundaries (\n\n) while keeping incomplete trailing data
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";

    for (const part of parts) {
      for (const line of part.split("\n")) {
        // SSE comments (": ...") are keepalive pings — skip them silently
        if (line.startsWith(":")) continue;
        if (!line.startsWith("data: ")) continue;
        try {
          const event = JSON.parse(line.slice(6));
          if (event.type === "chunk") {
            handlers.onChunk(event.text ?? "");
          } else if (event.type === "done") {
            handlers.onDone(event as ChatStreamDoneEvent);
          } else if (event.type === "error") {
            console.error("[StreamChat] server error event:", event.message);
            handlers.onError(event.message ?? "Unknown error");
          }
        } catch (parseErr) {
          console.warn(
            "[StreamChat] failed to parse SSE line:",
            line,
            parseErr,
          );
        }
      }
    }
  }
}

export const useStrategyChatMutation = () => {
  return useMutation({
    mutationFn: ({
      signal,
      ...body
    }: {
      strategy_id: string;
      message: string;
      history?: { role: string; content: string }[];
      signal?: AbortSignal;
    }) =>
      apiClient.post<
        ApiResponse<{
          strategy_id: string;
          explanation: string;
          prompt_proposal?: string;
          original_prompt?: string;
        }>
      >("/strategies/chat", body, { signal }),
  });
};

export const useUpdateStrategyPromptMutation = () => {
  return useMutation({
    mutationFn: (data: { strategy_id: string; prompt_text: string }) =>
      apiClient.patch<ApiResponse<{ strategy_id: string }>>(
        "/strategies/update-prompt",
        data,
      ),
  });
};

export const useGetPromptHistory = (
  strategyId?: string | number,
  enabled = true,
) => {
  return useQuery({
    queryKey: API_QUERY_KEYS.STRATEGY.promptHistory([strategyId ?? ""]),
    queryFn: () =>
      apiClient.get<ApiResponse<PromptVersion[]>>(
        `/strategies/prompt-history?id=${strategyId}`,
      ),
    select: (data) => data.data,
    enabled: !!strategyId && enabled,
  });
};

export const useStrategyPerformance = (strategyId: number | null) => {
  return useQuery({
    queryKey: API_QUERY_KEYS.STRATEGY.strategyPerformance(
      strategyId ? [strategyId] : [],
    ),
    queryFn: () =>
      apiClient.get<ApiResponse<StrategyPerformance>>(
        `/strategies/performance?id=${strategyId}`,
      ),
    select: (data) => data.data,
    enabled: false,
  });
};
