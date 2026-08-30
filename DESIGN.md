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
elevation:
  edge-light: "inset 0 -1px 0 rgba(28, 25, 23, 0.05)"
  edge-dark: "inset 0 1px 0 rgba(255, 255, 255, 0.05)"
  edge-accent: "inset 0 1px 0 rgba(255, 255, 255, 0.16)"
  raised: "0 1px 1px rgba(28, 25, 23, 0.04), 0 1px 3px -1px rgba(28, 25, 23, 0.08)"
  raised-hover: "0 1px 2px rgba(28, 25, 23, 0.05), 0 4px 10px -3px rgba(28, 25, 23, 0.1)"
  lifted: "0 2px 4px -1px rgba(28, 25, 23, 0.06), 0 12px 28px -10px rgba(28, 25, 23, 0.18)"
  overlay: "0 28px 90px rgba(15, 23, 42, 0.16)"
  well: "inset 0 1px 2px rgba(28, 25, 23, 0.07)"
  well-deep: "inset 0 2px 6px rgba(28, 25, 23, 0.1)"
  press: "inset 0 1px 2px rgba(28, 25, 23, 0.12)"
motion:
  press: "90ms"
  state: "160ms"
  enter: "240ms"
  signature: "380ms"
  ease-standard: "cubic-bezier(0.2, 0, 0, 1)"
  ease-exit: "cubic-bezier(0.4, 0, 1, 1)"
  ease-quint: "cubic-bezier(0.22, 1, 0.36, 1)"
  lift-hover: "-1px"
  lift-press: "1px"
  lift-card: "-2px"
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
- Depth under one overhead light: raised surfaces are actionable, recessed surfaces receive input.
- Precise state feedback for remote and asynchronous work, including motion that reports causality.
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

The interface is a machined console lit from directly above. Depth is a hairline affair: a 1px edge and a short shadow, never a diffuse halo, a gradient, or a glow. Light mode reads depth from the shadow beneath a surface and a faint dark line along its inner bottom edge; dark mode reads it from a faint white line along the inner top edge, with shadows recessed. Both themes express the same hierarchy.

Depth carries meaning rather than decoration, in exactly two states:

- **Raised means actionable.** Buttons, gallery cards, icon actions, panels, overlays.
- **Recessed means it receives input or content.** Fields, selects, parameter groups, image wells, the preview stage.

Disabled controls drop both: no edge, no shadow, so a dead control reads dead.

### Shadow Vocabulary

- **Edge** (\`inset 0 -1px 0 rgba(28, 25, 23, 0.05)\` light / \`inset 0 1px 0 rgba(255, 255, 255, 0.05)\` dark): the machined edge on every raised surface.
- **Edge Accent** (\`inset 0 1px 0 rgba(255, 255, 255, 0.16)\`): the lit top edge of a filled emerald control. It replaces a gradient; it is not one.
- **Raised** (\`--elev-1\`): panels, gallery cards, buttons, and icon actions at rest.
- **Raised Hover** (\`--elev-2\`): a control under the pointer, paired with a 1px rise.
- **Lifted** (\`--elev-3\`): gallery-card hover on fine pointers (paired with a 2px rise), toasts, and transient status panels.
- **Overlay** (\`--elev-4\`): drawers, dialogs, and the image lightbox.
- **Well** (\`inset 0 1px 2px\`): fields, selects, parameter groups, and image regions.
- **Well Deep** (\`inset 0 2px 6px\`): the preview stage once a result has seated into it.
- **Press** (\`inset 0 1px 2px rgba(28, 25, 23, 0.12)\`): the collapsed shadow of a control being pressed.

**The Lit-Deck Rule.** One light source, from above, everywhere. A resting *page section* still takes no shadow — only components participate. Never nest a raised surface inside a raised surface: a panel holds wells, and a well holds controls.

**The Ring Composition Rule.** Elevation is declared through \`--tw-shadow\` and applied as \`box-shadow: var(--tw-shadow)\`, so the keyboard focus ring composes with it instead of erasing it. A raised component that sets \`box-shadow\` directly will lose its focus ring.

## 5. Motion

Motion reports something true or it does not ship. Every animation in the product must encode either **causality** (this came from that) or **state change** (this just became something else). Ambient movement, scroll-triggered reveals, staggered list entrances, and looping decoration are forbidden.

### Duration and Easing

- **Press** (\`90ms\`): the depression of a control under the pointer.
- **State** (\`160ms\`): hover, colour, elevation, and small state changes.
- **Enter** (\`240ms\`): dialog and drawer arrival. Exits run at \`140ms\` — leaving is faster than arriving.
- **Signature** (\`380ms\`): the preview exposure, and nothing else.
- **Standard easing** \`cubic-bezier(0.2, 0, 0, 1)\` for arrival, **exit easing** \`cubic-bezier(0.4, 0, 1, 1)\` for departure, **quint** \`cubic-bezier(0.22, 1, 0.36, 1)\` for one-shot ticks.

### The Sanctioned Vocabulary

1. **Press physics.** Every raised control travels down 1px and collapses its shadow to the press inset. Recessed surfaces never move.
2. **Overlay choreography.** The overlay root carries the backdrop fade; the panel carries only its travel, so the two never compound. Dialogs scale from \`0.985\`. Drawers are revealed from the screen edge with a \`clip-path\` wipe — a right-anchored panel must never translate outward, because its box would leave the viewport.
3. **The exposure.** A generated result fades and scales up into the preview well while the well's inset deepens by one step. It runs once per result. This is the product's signature moment; nothing else may compete with it.
4. **State ticks.** One-shot, never looping: the favourite star on the way in only, the jobs badge on a count change, a running job row that just changed status, and the header shadow appearing once content scrolls beneath it.

**The Travel-Is-A-Token Rule.** Every translation reads from \`--lift-hover\`, \`--lift-press\`, or \`--motion-lift\`. Reduced motion sets those to \`0px\` rather than overriding \`transform\`, because \`@apply\` inlines component rules and an override can land in the wrong source order.

**The Reduced-Motion Rule.** Under \`prefers-reduced-motion: reduce\`, depth stays and travel goes to zero. Durations collapse via the tokens, and JavaScript-driven transitions check the media query directly so a leaving element never lingers.

## 6. Components

### Buttons

Buttons are restrained and operational.

- **Shape:** compact curved corners (\`6px\`) with a stable \`40px\` minimum height.
- **Primary:** Operational Emerald with high-contrast text and \`12–16px\` horizontal padding.
- **Hover / Focus:** one-step color strengthening plus a 1px rise on hover; a visible emerald focus ring with offset on keyboard focus.
- **Secondary:** surface background, default border, and secondary text; hover changes the surface and text tone and rises with the same 1px step.
- **Danger:** transparent or softly tinted surface with a red border and semantic danger text.
- **Icon-only:** at least \`44px × 44px\`; use a familiar icon and an accessible name.

### Chips

- **Style:** quiet bordered tags with \`6px\` corners, label typography, and \`4px 8px\` padding.
- **State:** passive tags use neutral surfaces; active or hovered prompt-helper tags may adopt a restrained emerald tint.

### Cards / Containers

- **Corner Style:** panels use \`8px\`; gallery cards use \`12px\`; dialogs may use \`16px\`.
- **Background:** theme-specific work surfaces and neutral layers.
- **Shadow Strategy:** raised at rest, one step only; nested groups are recessed. See the Elevation section.
- **Border:** a \`1px\` theme boundary is the default container separator.
- **Internal Padding:** \`16px\` on compact panels and \`20px\` on primary drawers and details.

### Inputs / Fields

- **Style:** \`40px\` minimum height, \`6px\` corners, a \`1px\` neutral border, canvas background, a well inset, and \`12px\` horizontal padding.
- **Focus:** retain the field shape and add a visible emerald ring or border shift; never remove focus without replacement.
- **Error / Disabled:** error states use the semantic danger family. Disabled controls retain legible labels and use opacity only as a secondary cue.

### Navigation

The sticky header is compact, border-led, and task-oriented. Product identity remains visible at the start; language, theme, reverse prompt, prompt snippets, jobs, and settings remain predictable commands. Mobile navigation wraps without reordering the workflow and preserves 44px touch targets.

### Gallery Card

The gallery card is the signature repeated object. It uses a \`12px\` bordered container raised one step, a recessed image region, compact metadata, and a row of 44px actions. Selected and favorite states use distinct semantics and an edge rather than extra shadow; hover lift is limited to fine pointers and goes to zero under reduced motion.

## 7. Do's and Don'ts

### Do:

- **Do** preserve the prompt → assistant → preview → gallery workflow order across viewport sizes.
- **Do** use Operational Emerald for primary action, active state, focus, and success.
- **Do** use neutral borders and tonal surfaces as the default hierarchy mechanism, with one step of elevation to separate what is actionable from what receives input.
- **Do** make every animation report causality or a state change, and read its travel from a token.
- **Do** keep common controls at \`40px\` and standalone icon targets at least \`44px × 44px\`.
- **Do** provide precise loading, success, error, empty, and disabled states.
- **Do** maintain WCAG 2.2 AA contrast, visible keyboard focus, accessible names, and reduced-motion behavior.

### Don't:

- **Don't** make the product feel like a neon creative toy.
- **Don't** use high-frequency animation, looping decoration, scroll-triggered reveals, staggered list entrances, or visually loud feedback.
- **Don't** introduce decorative gradients, glowing accents, soft-plastic bevels, or multiple competing signal colors.
- **Don't** add shadows to resting page sections, nest a raised surface inside a raised surface, or give a decorative element depth it has not earned.
- **Don't** use oversized display typography, fluid font sizing, or promotional hero composition in the working interface.
- **Don't** let mobile layouts reorder the core workflow, overflow horizontally, or shrink icon targets below \`44px\`.
