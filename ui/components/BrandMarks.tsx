"use client";

import { cx } from "./ui";

type MarkProps = {
  className?: string;
  title?: string;
};

/**
 * Brand marks as CSS masks so they inherit `currentColor`.
 * Source files live in `ui/public/brands/` (and legacy `/openai.svg`).
 */
function MaskMark({
  src,
  className,
  title,
}: MarkProps & { src: string }) {
  return (
    <span
      role="img"
      aria-label={title}
      title={title}
      className={cx("inline-block shrink-0 bg-current", className)}
      style={{
        maskImage: `url(${src})`,
        WebkitMaskImage: `url(${src})`,
        maskRepeat: "no-repeat",
        WebkitMaskRepeat: "no-repeat",
        maskPosition: "center",
        WebkitMaskPosition: "center",
        maskSize: "contain",
        WebkitMaskSize: "contain",
      }}
    />
  );
}

/** Map preset id → brand SVG under /brands. */
const PRESET_ICON: Record<string, string> = {
  custom: "/brands/custom.svg",
  "openai-api": "/brands/openai.svg",
  kadirr: "/brands/kadirr-mark.png",
  yunwu: "/brands/yunwu-mark.png",
  deepseek: "/brands/deepseek.svg",
  kimi: "/brands/kimi.svg",
  "kimi-coding": "/brands/kimi-coding.svg",
  openrouter: "/brands/openrouter.svg",
  minimax: "/brands/minimax.svg",
  "minimax-en": "/brands/minimax-en.svg",
  zai: "/brands/zai.svg",
  zhipu: "/brands/zhipu.svg",
  nvidia: "/brands/nvidia.svg",
  "opencode-go": "/brands/opencode.svg",
  mimo: "/brands/mimo.svg",
  azure: "/brands/azureai.svg",
  xai: "/brands/grok.svg",
};

export function OpenAILogo({ className, title = "OpenAI" }: MarkProps) {
  return <MaskMark src="/brands/openai.svg" className={className} title={title} />;
}

export function AnthropicLogo({ className, title = "Anthropic" }: MarkProps) {
  return <MaskMark src="/brands/anthropic.svg" className={className} title={title} />;
}

export function PresetIcon({
  presetId,
  name,
  className,
}: {
  presetId: string;
  name?: string;
  tint?: string;
  className?: string;
}) {
  const src = PRESET_ICON[presetId] ?? "/brands/custom.svg";
  return <MaskMark src={src} title={name ?? presetId} className={className} />;
}
