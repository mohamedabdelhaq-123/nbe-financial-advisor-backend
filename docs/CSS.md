# CSS
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Rules for CSS across the frontend: how colors, layout, and styling primitives are declared, and what's disallowed by default. This governs *how* styling is written; the actual visual language (palette, typography, component mapping) lives in the Design Guidelines (DaisyUI) document — this document is the enforcement layer underneath it.

---

## 1. No Inline Colors

No hex, rgb, hsl, or oklch value is ever written directly inside a component (`style={{ color: '#1a1a1a' }}`, `className="text-[#1a1a1a]"`, or a hardcoded value inside a component's own CSS). Every color reference goes through a DaisyUI semantic class (`text-primary`, `bg-error`) or a CSS variable defined once in `theme.css`. This is the single rule most likely to get broken under deadline pressure, so it is treated as non-negotiable rather than a style preference — a hardcoded hex value anywhere in component code is a review-blocking defect, not a nitpick.

**Why this matters here specifically:** the design goal is that a brand color change or a light/dark or contrast adjustment updates *everywhere* by editing one token. A single hardcoded hex value silently breaks that guarantee and is the kind of bug that's invisible until a rebrand or accessibility pass goes looking for it.

## 2. No Inline SVGs

SVG icon/illustration markup is never pasted directly into component JSX/HTML. Icons come from the agreed icon library (Lucide, per Frontend Decisions #25) as importable components, so only the icons actually used ship to the bundle, and any icon needing to change color follows the semantic-class rule in §1 rather than having its `fill`/`stroke` hardcoded inline. Any custom illustration or diagram artwork is a standalone `.svg` asset file, referenced, not inlined.

## 3. No Gradients

Backgrounds, buttons, and surfaces use flat, semantic color tokens — no CSS gradients (`linear-gradient`, `radial-gradient`) anywhere in component styling, including hover/active states. This keeps every surface's actual rendered color traceable to a single token rather than a blended, hard-to-name value, and keeps the palette consistent with the flat, semantic DaisyUI approach described in the Design Guidelines document.

## 4. Modern CSS, No Legacy Patterns

- **Logical properties for spacing/direction** (`margin-inline-start`, Tailwind's `ms-`/`me-`/`ps-`/`pe-` utilities), never physical left/right properties for anything that must flip under RTL (Frontend Decisions #6). A left/right utility (`ml-`, `pr-`, etc.) is a defect the moment Arabic layout is in scope, not a stylistic choice.
- **CSS custom properties (variables) over preprocessor variables** — no Sass/Less variable layer sits alongside Tailwind's `@theme` tokens; one source of truth for design tokens (see Design Guidelines §2).
- **No `!important`** as a way to win a specificity fight — if a style isn't applying, the component structure or class order is fixed, not overridden with `!important`.
- **No arbitrary one-off values for spacing/radius that already have a token** (`rounded-lg` hardcoded next to a `--radius-box` token that should have been used instead) — see Design Guidelines §4's "never a one-off `rounded-lg`" rule; this document is what makes that rule enforceable at the CSS-authoring level, not just a component-usage suggestion.
- **No inline `style` attributes for anything expressible as a utility class or token**, except where a value is genuinely computed at runtime (e.g. a progress-bar's dynamic width/fill percentage) — inline styles are the *exception* for runtime-computed values, not a shortcut for convenience.

## 5. Numerals & Bidi (cross-reference)

CSS is also responsible for enforcing the numerals rule from the Design Guidelines document: Western digits must render correctly even inside an RTL/Arabic text block. This is done via `font-variant-numeric` or a wrapping span carrying the English font family — never by fighting the Arabic font's own digit glyphs with a hack. This is a CSS-and-typography concern first; it should not be solved by injecting literal Latin-script substitute characters into Arabic strings at the data layer.

## 6. Enforcement

- **No hardcoded hex/gradient/inline-SVG should pass code review.** This is checked visually in review, not automated in v1 — if a linter for this becomes worth building later (e.g. a custom ESLint rule flagging hex-like strings in `className`/`style`), that's an addition to the CI pipeline (see DevOps documents), not a replacement for review discipline now.
- **RTL/EN screenshot QA per UI PR** (Frontend Decisions #20) is the practical backstop that catches most violations of §1 and §4's logical-properties rule in practice, since a hardcoded left/right property or a missed token usually shows up visibly once a screen is checked in both directions.
