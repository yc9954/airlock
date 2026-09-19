/* rakazo-style bot avatar: a coloured blob with two eyes, shape hashed from the
   identity, spinning ring while the lab is working (classes in index.css). */
import { memo, useId } from "react";
import { hashString } from "../format.ts";

const SHAPES = [
  // rounded hexagon
  "M50 4 L88 26 L88 74 L50 96 L12 74 L12 26 Z",
  // squircle
  "M50 4 C 82 4, 96 18, 96 50 C 96 82, 82 96, 50 96 C 18 96, 4 82, 4 50 C 4 18, 18 4, 50 4 Z",
  // circle
  "M50 4 A46 46 0 1 1 49.9 4 Z",
  // rounded diamond
  "M50 3 Q 60 3 66 12 L 88 34 Q 97 43 97 50 Q 97 57 88 66 L 66 88 Q 60 97 50 97 Q 40 97 34 88 L 12 66 Q 3 57 3 50 Q 3 43 12 34 L 34 12 Q 40 3 50 3 Z",
  // pebble
  "M50 6 C 76 2, 96 20, 94 48 C 92 78, 74 96, 48 94 C 20 92, 4 74, 6 46 C 8 20, 26 8, 50 6 Z",
];

export const BotAvatar = memo(function BotAvatar({
  color,
  identity,
  size = 36,
  working = false,
  className = "",
}: {
  color: string;
  identity: string;
  size?: number;
  working?: boolean;
  className?: string;
}) {
  const id = useId().replace(/[^a-zA-Z0-9-_]/g, "");
  const shape = SHAPES[hashString(identity) % SHAPES.length] ?? SHAPES[0]!;
  const dark = `color-mix(in oklab, ${color}, black 38%)`;
  const light = `color-mix(in oklab, ${color}, white 12%)`;
  return (
    <div
      className={`rakazo-bot-avatar relative inline-flex shrink-0 select-none items-center justify-center ${className}`}
      style={{ width: size, height: size }}
      data-working={working ? "true" : "false"}
    >
      <svg
        className="rakazo-bot-avatar-ring pointer-events-none absolute"
        style={{ inset: -4, width: size + 8, height: size + 8, filter: `drop-shadow(0 0 6px ${light})` }}
        viewBox="0 0 48 48"
        fill="none"
        aria-hidden="true"
      >
        <circle cx="24" cy="24" r="22" stroke={`url(#${id}-ring)`} strokeWidth="3.2" strokeLinecap="round" strokeDasharray="45 80" />
        <circle cx="43" cy="24" r="2.8" fill="#ffffff" />
        <defs>
          <linearGradient id={`${id}-ring`} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#ffffff" stopOpacity="1" />
            <stop offset="60%" stopColor={light} stopOpacity="0.9" />
            <stop offset="100%" stopColor={light} stopOpacity="0" />
          </linearGradient>
        </defs>
      </svg>
      <svg
        viewBox="0 0 100 100"
        width={size}
        height={size}
        aria-hidden="true"
        className={`overflow-visible transition-transform duration-300 ${working ? "scale-[1.04]" : ""}`}
        style={{ filter: working ? `drop-shadow(0 0 8px ${light})` : "drop-shadow(0 2px 4px rgba(0,0,0,0.45))" }}
      >
        <defs>
          <linearGradient id={`${id}-ink`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor={light} />
            <stop offset="100%" stopColor={dark} />
          </linearGradient>
        </defs>
        <path d={shape} fill={`url(#${id}-ink)`} />
        <g fill="#ffffff">
          <ellipse cx="36" cy="46" rx="7" ry="9" />
          <ellipse cx="64" cy="46" rx="7" ry="9" />
        </g>
      </svg>
    </div>
  );
});
