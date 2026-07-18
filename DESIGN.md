---
name: GPT Image Panel
description: A calm, precise control surface for self-hosted GPT-compatible image workflows.
colors:
  operational-emerald: "#059669"
  operational-emerald-hover: "#047857"
  operational-emerald-dark: "#10B981"
  operational-emerald-dark-hover: "#34D399"
  canvas-light: "#FAFAF9"
  surface-light: "#FFFFFF"
  layer-light: "#F5F5F4"
  ink-light: "#1C1917"
  muted-light: "#57534E"
  border-light: "#D6D3D1"
  canvas-dark: "#09090B"
  surface-dark: "#18181B"
  layer-dark: "#27272A"
  ink-dark: "#F4F4F5"
  muted-dark: "#A1A1AA"
  border-dark: "#3F3F46"
  danger-light: "#B91C1C"
  danger-dark: "#FECACA"
  favorite-light: "#D97706"
  favorite-dark: "#FBBF24"
typography:
  headline:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "18px"
    fontWeight: 600
    lineHeight: 1.55
    letterSpacing: "normal"
  title:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.43
    letterSpacing: "normal"
  body:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.43
    letterSpacing: "normal"
  label:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.33
    letterSpacing: "normal"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.43
    letterSpacing: "normal"
rounded:
  control: "6px"
  surface: "8px"
  card: "12px"
  dialog: "16px"
  pill: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  2xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.operational-emerald}"
    textColor: "{colors.surface-light}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
    height: "40px"
  button-primary-hover:
    backgroundColor: "{colors.operational-emerald-hover}"
    textColor: "{colors.surface-light}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
    height: "40px"
  button-secondary:
    backgroundColor: "{colors.surface-light}"
    textColor: "{colors.muted-light}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
    height: "40px"
  field:
    backgroundColor: "{colors.canvas-light}"
    textColor: "{colors.ink-light}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
    height: "40px"
  surface:
    backgroundColor: "{colors.surface-light}"
    textColor: "{colors.ink-light}"
    rounded: "{rounded.surface}"
    padding: "16px"
  prompt-chip:
    backgroundColor: "{colors.surface-light}"
    textColor: "{colors.muted-light}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "4px 8px"
---

# Design System: GPT Image Panel

## 1. Overview

**Creative North Star: "The Quiet Control Room"**

GPT Image Panel is a restrained operational workspace. Its visual system keeps the user's prompt, configuration, task state, preview, and image library in one continuous field of attention. Neutral stone and zinc surfaces carry most of the interface; Operational Emerald appears only where a decision, focus state, or active operation needs a clear signal.

The interface is quiet, disciplined, and operator-focused. Density is welcome when it improves scanning and repeated action, but every control must remain predictable and legible. The system explicitly rejects the neon creative-toy aesthetic: high-frequency animation, exaggerated gamification, and visually loud feedback are forbidden.

**Key Characteristics:**

- Calm neutral surfaces in both light and dark themes.
- Compact controls with stable 40px heights and 44px touch targets where icons stand alone.
- Border-led hierarchy, with shadows reserved for overlays and meaningful lift.
- Precise state feedback for remote and asynchronous work.
- Responsive structures that preserve workflow order from desktop to mobile.

## 2. Colors

The palette is a neutral control surface with one operational signal color and a small semantic vocabulary.

### Primary

- **Operational Emerald** (\`operational-emerald\` / \`operational-emerald-dark\`): primary commands, active controls, focus emphasis, and successful operational states.
- **Operational Emerald Hover** (\`operational-emerald-hover\` / \`operational-emerald-dark-hover\`): the only approved stronger green for direct interaction feedback.

### Neutral

- **Stone Canvas** (\`canvas-light\`): the light application background and field base.
- **White Work Surface** (\`surface-light\`): primary panels, cards, drawers, and button surfaces in light mode.
- **Quiet Stone Layer** (\`layer-light\`): secondary grouping, hover, and subtle separation in light mode.
- **Stone Ink** (\`ink-light\`): primary light-mode text.
- **Muted Stone** (\`muted-light\`): supporting text and inactive controls.
- **Stone Boundary** (\`border-light\`): default light-mode field and panel borders.
- **Zinc Canvas** (\`canvas-dark\`): the dark application background and field base.
- **Zinc Work Surface** (\`surface-dark\`): primary dark-mode panels and drawers.
- **Raised Zinc Layer** (\`layer-dark\`): dark hover, selected tabs, and nested grouping.
- **Zinc Light** (\`ink-dark\`): primary dark-mode text.
- **Muted Zinc** (\`muted-dark\`): supporting dark-mode text.
- **Zinc Boundary** (\`border-dark\`): default dark-mode field and panel borders.

### Semantic

- **Measured Danger** (\`danger-light\` / \`danger-dark\`): destructive actions and failure text. It is never decorative.
- **Favorite Amber** (\`favorite-light\` / \`favorite-dark\`): the specific saved/favorite state and no other general emphasis.

**The One Signal Rule.** Operational Emerald is reserved for primary action, active state, focus, and success. Do not spread it across passive decoration.

**The Theme Parity Rule.** Light and dark themes must preserve the same hierarchy and semantics even when their absolute colors differ.

## 3. Typography

**Display Font:** None. Product screens do not use display typography.
**Body Font:** System UI with the native platform sans-serif fallback stack.
**Label/Mono Font:** System UI for labels; the native monospace stack for API paths, models, and machine values.

**Character:** The type system is compact, neutral, and optimized for repeated operational reading. Weight and spacing establish hierarchy; novelty does not.

### Hierarchy

- **Headline** (600, 18px, 1.55): drawer and dialog titles only.
- **Title** (600, 14px, 1.43): panel headings, compact section titles, and important row labels.
- **Body** (400, 14px, 1.43): form values, descriptions, job details, and operational copy.
- **Label** (500, 12px, 1.33): field labels, metadata, helper text, and compact status text.
- **Mono** (400, 14px, 1.43): API paths, model identifiers, environment-facing configuration, and other machine-readable values.

**The Interface-First Rule.** Never introduce a display font, fluid type scale, negative letter spacing, or decorative capitalization into product controls.

## 4. Elevation

The system is flat by default. Borders and tonal layers define normal page hierarchy; shadows create structural separation only when an element leaves the document flow or responds to hover. Light mode uses soft ambient shadows for drawers, dialogs, toasts, and the image lightbox. Dark mode relies more heavily on tonal contrast and may suppress nonessential shadows.

### Shadow Vocabulary

- **Overlay Light** (\`0 28px 90px rgba(15, 23, 42, 0.16)\`): the large lightbox and major modal surfaces.
- **Overlay Dark** (\`0 28px 90px rgba(0, 0, 0, 0.65)\`): the corresponding dark lightbox separation.
- **Transient Panel** (\`0 12px 28px rgba(15, 23, 42, 0.16)\`): lazy-loading and compact transient status surfaces.
- **Gallery Hover** (\`0 8px 30px rgba(0, 0, 0, 0.5)\`): image-card hover on fine pointers only, paired with a 2px upward translation.

**The Flat-by-Default Rule.** A resting page section does not receive a shadow. If an element is neither overlaying content nor responding to interaction, use a border or tonal layer instead.

## 5. Components

### Buttons

Buttons are restrained and operational.

- **Shape:** compact curved corners (\`6px\`) with a stable \`40px\` minimum height.
- **Primary:** Operational Emerald with high-contrast text and \`12–16px\` horizontal padding.
- **Hover / Focus:** one-step color strengthening on hover; a visible emerald focus ring with offset on keyboard focus.
- **Secondary:** surface background, default border, and secondary text; hover changes the surface and text tone without adding lift.
- **Danger:** transparent or softly tinted surface with a red border and semantic danger text.
- **Icon-only:** at least \`44px × 44px\`; use a familiar icon and an accessible name.

### Chips

- **Style:** quiet bordered tags with \`6px\` corners, label typography, and \`4px 8px\` padding.
- **State:** passive tags use neutral surfaces; active or hovered prompt-helper tags may adopt a restrained emerald tint.

### Cards / Containers

- **Corner Style:** panels use \`8px\`; gallery cards use \`12px\`; dialogs may use \`16px\`.
- **Background:** theme-specific work surfaces and neutral layers.
- **Shadow Strategy:** flat at rest; see the Elevation section.
- **Border:** a \`1px\` theme boundary is the default container separator.
- **Internal Padding:** \`16px\` on compact panels and \`20px\` on primary drawers and details.

### Inputs / Fields

- **Style:** \`40px\` minimum height, \`6px\` corners, a \`1px\` neutral border, canvas background, and \`12px\` horizontal padding.
- **Focus:** retain the field shape and add a visible emerald ring or border shift; never remove focus without replacement.
- **Error / Disabled:** error states use the semantic danger family. Disabled controls retain legible labels and use opacity only as a secondary cue.

### Navigation

The sticky header is compact, border-led, and task-oriented. Product identity remains visible at the start; language, theme, reverse prompt, prompt snippets, jobs, and settings remain predictable commands. Mobile navigation wraps without reordering the workflow and preserves 44px touch targets.

### Gallery Card

The gallery card is the signature repeated object. It uses a \`12px\` bordered container, stable image region, compact metadata, and a row of 44px actions. Selected and favorite states use distinct semantics; hover lift is limited to fine pointers and is disabled under reduced motion.

## 6. Do's and Don'ts

### Do:

- **Do** preserve the prompt → assistant → preview → gallery workflow order across viewport sizes.
- **Do** use Operational Emerald for primary action, active state, focus, and success.
- **Do** use neutral borders and tonal surfaces as the default hierarchy mechanism.
- **Do** keep common controls at \`40px\` and standalone icon targets at least \`44px × 44px\`.
- **Do** provide precise loading, success, error, empty, and disabled states.
- **Do** maintain WCAG 2.2 AA contrast, visible keyboard focus, accessible names, and reduced-motion behavior.

### Don't:

- **Don't** make the product feel like a neon creative toy.
- **Don't** use high-frequency animation, exaggerated gamification, or visually loud feedback.
- **Don't** introduce decorative gradients, glowing accents, or multiple competing signal colors.
- **Don't** add shadows to resting page sections or nest decorative cards inside cards.
- **Don't** use oversized display typography, fluid font sizing, or promotional hero composition in the working interface.
- **Don't** let mobile layouts reorder the core workflow, overflow horizontally, or shrink icon targets below \`44px\`.
