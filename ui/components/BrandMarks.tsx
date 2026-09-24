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

/**
 * A brand mark that has to keep its own artwork.
 *
 * `MaskMark` drives the shape from alpha, which is right for a flat glyph and
 * wrong for anything with detail inside its silhouette: the Hermes logo is an
 * opaque black-and-white illustration, so masking it renders a solid square.
 */
function ImageMark({ src, className, title }: MarkProps & { src: string }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element -- static export, no loader
    <img
      src={src}
      alt=""
      role="img"
      aria-label={title}
      title={title}
      className={cx("inline-block shrink-0 rounded-[3px] object-contain", className)}
    />
  );
}

/** Map preset id → brand SVG under /brands. */
const PRESET_ICON: Record<string, string> = {
  custom: "/brands/custom.svg",
  anthropic: "/brands/anthropic.svg",
  "openai-api": "/brands/openai.svg",
  gemini: "/brands/google.svg",
  groq: "/brands/groq.svg",
  mistral: "/brands/mistral.svg",
  together: "/brands/together.svg",
  fireworks: "/brands/fireworks.svg",
  venice: "/brands/venice.svg",
  codefast: "/brands/codefast.svg",
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
  "kimi-for-coding": "/brands/kimi-coding.svg",
  mimo: "/brands/mimo.svg",
  azure: "/brands/azureai.svg",
  xai: "/brands/grok.svg",
};

export function OpenAILogo({ className, title = "OpenAI" }: MarkProps) {
  return <MaskMark src="/brands/openai.svg" className={className} title={title} />;
}

export function OpenCodeLogo({ className, title = "OpenCode" }: MarkProps) {
  return <MaskMark src="/brands/opencode.svg" className={className} title={title} />;
}

export function HermesLogo({ className, title = "Hermes" }: MarkProps) {
  return <ImageMark src="/brands/hermes.png" className={className} title={title} />;
}

export function AnthropicLogo({ className, title = "Anthropic" }: MarkProps) {
  return <MaskMark src="/brands/anthropic.svg" className={className} title={title} />;
}

/**
 * Drawn as three faces with the seams left transparent, so the mask keeps the
 * faceted cube. A solid silhouette of the same outline is just a hexagon.
 */
export function CursorLogo({ className, title = "Cursor" }: MarkProps) {
  return <MaskMark src="/brands/cursor.svg" className={className} title={title} />;
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
