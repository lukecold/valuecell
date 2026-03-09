import { useMutation, useQuery } from "@tanstack/react-query";
import { API_QUERY_KEYS } from "@/constants/api";
import { type ApiResponse, apiClient } from "@/lib/api-client";
import { useSystemStore } from "@/store/system-store";
import type { SystemInfo } from "@/types/system";

// ── Request magic link ────────────────────────────────────────────────────────

export const useRequestMagicLink = () => {
  return useMutation({
    mutationFn: (email: string) =>
      apiClient.post<ApiResponse<{ email: string }>>("/auth/magic-link", {
        email,
      }),
  });
};

// ── Verify magic link token ───────────────────────────────────────────────────

export type VerifyResponse = Pick<
  SystemInfo,
  | "id"
  | "email"
  | "name"
  | "avatar"
  | "access_token"
  | "refresh_token"
  | "created_at"
  | "updated_at"
>;

export const useVerifyMagicLink = (token: string | null) => {
  return useQuery({
    queryKey: ["auth", "verify", token],
    queryFn: () =>
      apiClient.get<ApiResponse<VerifyResponse>>(
        `/auth/verify?token=${token}`,
        { requiresAuth: false },
      ),
    enabled: !!token,
    retry: false,
    select: (data) => data.data,
  });
};

// ── Current user info ─────────────────────────────────────────────────────────

export const useGetMe = (enabled = true) => {
  return useQuery({
    queryKey: API_QUERY_KEYS.AUTH.me,
    queryFn: () =>
      apiClient.get<ApiResponse<VerifyResponse>>("/auth/me", {
        requiresAuth: true,
      }),
    enabled,
    select: (data) => data.data,
    retry: false,
  });
};

// ── Claim unclaimed strategies ────────────────────────────────────────────────

export const useClaimStrategies = () => {
  return useMutation({
    mutationFn: () =>
      apiClient.post<ApiResponse<{ claimed: number; email: string }>>(
        "/auth/claim-strategies",
        {},
        { requiresAuth: true },
      ),
  });
};

// ── Logout (client-side only) ─────────────────────────────────────────────────

export const useLogout = () => {
  const clearSystemInfo = useSystemStore((s) => s.clearSystemInfo);
  return () => clearSystemInfo();
};
