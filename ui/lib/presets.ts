import type { AppId, AuthStyle, WireApi } from "./types";

export type ProviderPreset = {
  id: string;
  name: string;
  website: string;
  base_url: string;
  model?: string;
  small_fast_model?: string;
  auth_style?: AuthStyle;
  wire_api?: WireApi;
  env_key?: string;
  note?: string;
  /** Extra env vars (Claude) or Codex provider/top-level keys. */
  extra?: Record<string, string>;
  /** Accent for the preset chip. */
  tint?: string;
};

/**
 * Curated presets for Claude Code & Codex.
 * OpenAI API (Codex), Kadirr.Dev (Codex), Yunwu, DeepSeek, Kimi, Kimi Coding,
 * OpenRouter, MiniMax CH/Global, Z.ai, Zhipu GLM, Nvidia NIM, OpenCode Go,
 * Xiaomi MiMo, Azure, xAI (+ Custom).
 */
export const CLAUDE_PRESETS: ProviderPreset[] = [
  {
    id: "custom",
    name: "Custom",
    website: "",
    base_url: "",
    note: "Blank template",
    tint: "#8E8E93",
  },
  {
    id: "yunwu",
    name: "Yunwu",
    website: "https://yunwu.ai",
    base_url: "https://api.yunwu.cloud",
    model: "claude-sonnet-4-5",
    auth_style: "auth_token",
    note: "Claude Code relay — paste sk- token from Yunwu panel",
    tint: "#2563EB",
    extra: { API_TIMEOUT_MS: "300000" },
  },
  {
    id: "deepseek",
    name: "DeepSeek",
    website: "https://platform.deepseek.com",
    base_url: "https://api.deepseek.com/anthropic",
    model: "deepseek-v4-pro",
    tint: "#1E88E5",
  },
  {
    id: "kimi",
    name: "Kimi",
    website: "https://platform.kimi.com",
    base_url: "https://api.moonshot.cn/anthropic",
    model: "kimi-k2.7-code",
    tint: "#6366F1",
  },
  {
    id: "kimi-coding",
    name: "Kimi For Coding",
    website: "https://www.kimi.com/code",
    base_url: "https://api.kimi.com/coding",
    model: "kimi-for-coding",
    tint: "#6366F1",
  },
  {
    id: "openrouter",
    name: "OpenRouter",
    website: "https://openrouter.ai",
    base_url: "https://openrouter.ai/api",
    model: "anthropic/claude-sonnet-4",
    tint: "#6566F1",
  },
  {
    id: "minimax",
    name: "MiniMax CH",
    website: "https://platform.minimaxi.com",
    base_url: "https://api.minimaxi.com/anthropic",
    model: "MiniMax-M2.7",
    tint: "#FF6B6B",
  },
  {
    id: "minimax-en",
    name: "MiniMax Global",
    website: "https://platform.minimax.io",
    base_url: "https://api.minimax.io/anthropic",
    model: "MiniMax-M2.7",
    tint: "#FF6B6B",
  },
  {
    id: "zai",
    name: "Z.ai",
    website: "https://z.ai",
    base_url: "https://api.z.ai/api/anthropic",
    model: "glm-5.1",
    tint: "#0F62FE",
  },
  {
    id: "zhipu",
    name: "Zhipu GLM",
    website: "https://open.bigmodel.cn",
    base_url: "https://open.bigmodel.cn/api/anthropic",
    model: "glm-5.1",
    tint: "#0F62FE",
  },
  {
    id: "nvidia",
    name: "Nvidia NIM",
    website: "https://build.nvidia.com",
    base_url: "https://integrate.api.nvidia.com",
    model: "moonshotai/kimi-k2.5",
    tint: "#76B900",
  },
  {
    id: "opencode-go",
    name: "OpenCode Go",
    website: "https://opencode.ai/go",
    base_url: "https://opencode.ai/zen/go",
    model: "deepseek-v4-flash",
    tint: "#F97316",
  },
  {
    id: "mimo",
    name: "Xiaomi MiMo",
    website: "https://platform.xiaomimimo.com",
    base_url: "https://api.xiaomimimo.com/anthropic",
    model: "mimo-v2.5-pro",
    tint: "#FF6900",
  },
  {
    id: "azure",
    name: "Azure OpenAI",
    website: "https://learn.microsoft.com/azure/ai-foundry/openai/how-to/codex",
    base_url: "https://YOUR_RESOURCE_NAME.openai.azure.com/openai",
    model: "gpt-5-codex",
    note: "Replace YOUR_RESOURCE_NAME",
    tint: "#0078D4",
  },
  {
    id: "xai",
    name: "xAI Grok",
    website: "https://x.ai/grok",
    base_url: "https://api.x.ai/v1",
    model: "grok-4.5",
    tint: "#111111",
  },
];

export const CODEX_PRESETS: ProviderPreset[] = [
  {
    id: "custom",
    name: "Custom",
    website: "",
    base_url: "",
    note: "Blank template",
    tint: "#8E8E93",
  },
  {
    id: "openai-api",
    name: "OpenAI API",
    website: "https://platform.openai.com",
    base_url: "https://api.openai.com/v1",
    model: "gpt-5-codex",
    wire_api: "responses",
    env_key: "OPENAI_API_KEY",
    note: "Official OpenAI API",
    tint: "#10A37F",
  },
  {
    id: "kadirr",
    name: "Kadirr.Dev",
    website: "https://kadirr.dev",
    base_url: "https://claude.kadirr.dev",
    model: "gpt-5.6-sol",
    wire_api: "responses",
    env_key: "OPENAI_API_KEY",
    note: "Models: gpt-5.6-sol / terra / luna, gpt-5.5 — no /v1 on base URL",
    tint: "#0F172A",
    extra: {
      requires_openai_auth: "true",
      review_model: "gpt-5.6-sol",
      model_reasoning_effort: "xhigh",
      disable_response_storage: "true",
      network_access: "enabled",
    },
  },
  {
    id: "yunwu",
    name: "Yunwu",
    website: "https://yunwu.ai",
    base_url: "https://yunwu.ai/v1",
    model: "gpt-5-codex",
    wire_api: "responses",
    env_key: "OPENAI_API_KEY",
    note: "Yunwu OpenAI-compatible Responses API",
    tint: "#2563EB",
    extra: {
      model_reasoning_effort: "high",
      disable_response_storage: "true",
      preferred_auth_method: "apikey",
    },
  },
  {
    id: "deepseek",
    name: "DeepSeek",
    website: "https://platform.deepseek.com",
    base_url: "https://api.deepseek.com",
    model: "deepseek-v4-flash",
    wire_api: "chat",
    tint: "#1E88E5",
  },
  {
    id: "kimi",
    name: "Kimi",
    website: "https://platform.kimi.com",
    base_url: "https://api.moonshot.cn/v1",
    model: "kimi-k2.7-code",
    wire_api: "responses",
    tint: "#6366F1",
  },
  {
    id: "kimi-coding",
    name: "Kimi For Coding",
    website: "https://www.kimi.com/code",
    base_url: "https://api.kimi.com/coding/v1",
    model: "kimi-for-coding",
    wire_api: "responses",
    tint: "#6366F1",
  },
  {
    id: "openrouter",
    name: "OpenRouter",
    website: "https://openrouter.ai",
    base_url: "https://openrouter.ai/api/v1",
    model: "openai/gpt-5-codex",
    wire_api: "responses",
    tint: "#6566F1",
  },
  {
    id: "minimax",
    name: "MiniMax CH",
    website: "https://platform.minimaxi.com",
    base_url: "https://api.minimaxi.com/v1",
    model: "MiniMax-M2.7",
    wire_api: "responses",
    tint: "#FF6B6B",
  },
  {
    id: "minimax-en",
    name: "MiniMax Global",
    website: "https://platform.minimax.io",
    base_url: "https://api.minimax.io/v1",
    model: "MiniMax-M2.7",
    wire_api: "responses",
    tint: "#FF6B6B",
  },
  {
    id: "zai",
    name: "Z.ai",
    website: "https://z.ai",
    base_url: "https://api.z.ai/api/coding/paas/v4",
    model: "glm-5.2",
    wire_api: "chat",
    tint: "#0F62FE",
  },
  {
    id: "zhipu",
    name: "Zhipu GLM",
    website: "https://open.bigmodel.cn",
    base_url: "https://open.bigmodel.cn/api/coding/paas/v4",
    model: "glm-5.2",
    wire_api: "chat",
    tint: "#0F62FE",
  },
  {
    id: "nvidia",
    name: "Nvidia NIM",
    website: "https://build.nvidia.com",
    base_url: "https://integrate.api.nvidia.com/v1",
    model: "moonshotai/kimi-k2.5",
    wire_api: "chat",
    tint: "#76B900",
  },
  {
    id: "opencode-go",
    name: "OpenCode Go",
    website: "https://opencode.ai/go",
    base_url: "https://opencode.ai/zen/go/v1",
    model: "deepseek-v4-flash",
    wire_api: "chat",
    tint: "#F97316",
  },
  {
    id: "mimo",
    name: "Xiaomi MiMo",
    website: "https://platform.xiaomimimo.com",
    base_url: "https://api.xiaomimimo.com/v1",
    model: "mimo-v2.5-pro",
    wire_api: "responses",
    tint: "#FF6900",
  },
  {
    id: "azure",
    name: "Azure OpenAI",
    website: "https://learn.microsoft.com/azure/ai-foundry/openai/how-to/codex",
    base_url: "https://YOUR_RESOURCE_NAME.openai.azure.com/openai",
    model: "gpt-5-codex",
    wire_api: "responses",
    note: "Replace YOUR_RESOURCE_NAME",
    tint: "#0078D4",
  },
  {
    id: "xai",
    name: "xAI Grok",
    website: "https://x.ai/api",
    base_url: "https://api.x.ai/v1",
    model: "grok-4",
    wire_api: "responses",
    tint: "#111111",
  },
];

export function presetsFor(app: AppId): ProviderPreset[] {
  return app === "claude" ? CLAUDE_PRESETS : CODEX_PRESETS;
}
