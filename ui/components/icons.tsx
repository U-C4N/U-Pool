/**
 * Hand-rolled icons — SF Symbols–inspired strokes.
 * Kept in-repo to avoid an icon-package dependency for a small set.
 */
type IconProps = React.SVGProps<SVGSVGElement>;

function Svg({ children, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

/** Add — rounded plus. */
export const PlusIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 6.5v11M6.5 12h11" />
  </Svg>
);

/** Settings — horizontal sliders (SF: slider.horizontal.3). */
export const GearIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 7h10M18 7h2M4 17h2M10 17h10" />
    <path d="M4 12h4M12 12h8" />
    <circle cx="16" cy="7" r="2" />
    <circle cx="8" cy="17" r="2" />
    <circle cx="10" cy="12" r="2" />
  </Svg>
);

/** Edit — fountain pen tip. */
export const PencilIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m14.5 4.5 5 5" />
    <path d="M5 19.5 17.5 7 14.5 4 2 16.5V19.5H5Z" />
    <path d="M11 5.5 16 10.5" />
  </Svg>
);

/** Duplicate — stacked rectangles. */
export const CopyIcon = (p: IconProps) => (
  <Svg {...p}>
    <rect x="8" y="8" width="11" height="11" rx="2.5" />
    <path d="M6 15.5H5.5A2.5 2.5 0 0 1 3 13V5.5A2.5 2.5 0 0 1 5.5 3H13a2.5 2.5 0 0 1 2.5 2.5V6" />
  </Svg>
);

/** Test connection — signal / radiowaves. */
export const PulseIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 18.5v.01" />
    <path d="M8.5 15.2a5 5 0 0 1 7 0" />
    <path d="M5.5 12.2a9 9 0 0 1 13 0" />
    <path d="M2.8 9.2a13 13 0 0 1 18.4 0" />
  </Svg>
);

/** Delete — trash with lid lift. */
export const TrashIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4.5 7h15" />
    <path d="M9.5 7V5.8A1.3 1.3 0 0 1 10.8 4.5h2.4A1.3 1.3 0 0 1 14.5 5.8V7" />
    <path d="M18 7v11.2A1.8 1.8 0 0 1 16.2 20H7.8A1.8 1.8 0 0 1 6 18.2V7" />
    <path d="M10 10.5v5.5M14 10.5v5.5" />
  </Svg>
);

export const CheckIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m5.5 12.5 4 4 9-9.5" />
  </Svg>
);

export const CheckCircleIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.25" />
    <path d="m8.4 12.2 2.4 2.4 4.8-5" />
  </Svg>
);

export const AlertIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 4.2 20.2 19.2a.9.9 0 0 1-.78 1.35H4.58a.9.9 0 0 1-.78-1.35L12 4.2Z" />
    <path d="M12 10v4.2M12 16.8h.01" />
  </Svg>
);

export const XIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m7 7 10 10M17 7 7 17" />
  </Svg>
);

export const ArrowLeftIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M15.5 5.5 9 12l6.5 6.5" />
    <path d="M9 12h10" />
  </Svg>
);

export const ChevronIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m9.5 6 5.5 6-5.5 6" />
  </Svg>
);

/** Open config — open folder with papers. */
export const FolderIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3.5 9.5V7.2A1.7 1.7 0 0 1 5.2 5.5h4.1l1.6 1.7h7.9A1.7 1.7 0 0 1 20.5 9v.5" />
    <path d="M3.5 10.5h17l-1.4 7.2a1.7 1.7 0 0 1-1.7 1.4H6.6a1.7 1.7 0 0 1-1.7-1.4L3.5 10.5Z" />
  </Svg>
);

export const GripIcon = (p: IconProps) => (
  <Svg {...p} strokeWidth={0} fill="currentColor">
    <circle cx="9" cy="7" r="1.35" />
    <circle cx="9" cy="12" r="1.35" />
    <circle cx="9" cy="17" r="1.35" />
    <circle cx="15" cy="7" r="1.35" />
    <circle cx="15" cy="12" r="1.35" />
    <circle cx="15" cy="17" r="1.35" />
  </Svg>
);

export const EyeIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M2.5 12s3.8-6.5 9.5-6.5S21.5 12 21.5 12 17.7 18.5 12 18.5 2.5 12 2.5 12Z" />
    <circle cx="12" cy="12" r="2.75" />
  </Svg>
);

export const EyeOffIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m3.5 3.5 17 17" />
    <path d="M10.2 10.3a2.75 2.75 0 0 0 3.5 3.5" />
    <path d="M8.1 5.6A10 10 0 0 1 12 5.5c5.7 0 9.5 6.5 9.5 6.5a16 16 0 0 1-3.5 4.2" />
    <path d="M6.2 6.8A16 16 0 0 0 2.5 12S6.3 18.5 12 18.5a9.4 9.4 0 0 0 3.9-.8" />
  </Svg>
);

/** Claude / Anthropic starburst mark. */
export const ClaudeGlyph = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" {...p}>
    <path d="M12 2.5c.4 0 .7.3.75.68l.6 5.1 3.2-3.4a.75.75 0 0 1 1.12 1l-3.1 3.6 4.86-1.4a.75.75 0 0 1 .42 1.44l-4.9 1.44 4.9 1.44a.75.75 0 0 1-.42 1.44l-4.86-1.4 3.1 3.6a.75.75 0 1 1-1.12 1l-3.2-3.4-.6 5.1a.75.75 0 0 1-1.5 0l-.6-5.1-3.2 3.4a.75.75 0 1 1-1.12-1l3.1-3.6-4.86 1.4a.75.75 0 1 1-.42-1.44L9 12l-4.9-1.44a.75.75 0 0 1 .42-1.44l4.86 1.4-3.1-3.6a.75.75 0 0 1 1.12-1l3.2 3.4.6-5.1A.75.75 0 0 1 12 2.5Z" />
  </svg>
);

/** Inline fallback if `/openai.svg` is unavailable. Prefer `OpenAILogo` from BrandMarks. */
export const OpenAIGlyph = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" {...p}>
    <path d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.1419.0852 4.783-2.7582a.7712.7712 0 0 0 .7806 0l5.8428 3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9723V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.1419-.0852-4.783-2.7622a.7956.7956 0 0 0-.7856 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z" />
  </svg>
);

export const CodexGlyph = OpenAIGlyph;
