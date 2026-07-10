# Akilent Brand Color System

Akilent is enterprise communications infrastructure. The palette communicates
reliability, precision, and quiet confidence — not marketing flash. Reference
points: Stripe, Linear, Vercel, Twilio.

**The 70/20/10 rule.** Interfaces are built from neutrals first:

- **70–80 % neutrals** — Paper canvas, white surfaces, navy-tinted grays
- **15–20 % functional accents** — channel colors (teal = WhatsApp, amber = email), navy brand
- **5–10 % status colors** — success, warning, error, info

Color communicates **meaning**, never decoration. Every accent has a job.

---

## 1. Core Palette

| Name | Hex | Role |
|---|---|---|
| **Night Ops Navy** | `#0E1526` | Primary brand. Nav, heroes, dark surfaces, console backgrounds. Never pure black. |
| **Paper** | `#F3F5F7` | Light workspace / dashboard canvas. |
| **Ink** | `#12182B` | Primary text color (light theme). |
| **Transmit Teal** | `#1FAF9C` | WhatsApp channel · delivered/read · success · active integrations. |
| **Signal Amber** | `#FFB020` | Email channel · primary CTAs · important actions. |
| **Alert Coral** | `#E8604A` | Errors, failed delivery, destructive actions **only**. Never decorative. |

## 2. Neutral Scale

Navy-tinted neutrals. Overrides Tailwind's `gray-*` in `assets/app.css`, so all
existing `gray-*` utilities resolve to these values.

| Step | Hex | Usage |
|---|---|---|
| 50 | `#FAFBFC` | Muted surfaces (`surface-muted`), table headers |
| 100 | `#F3F5F7` | Paper canvas, hover fills, code backgrounds |
| 200 | `#E8EDF3` | Default borders, dividers, gridlines |
| 300 | `#DCE1E8` | Strong borders, input outlines |
| 400 | `#A8B2C4` | Placeholders, disabled text, decorative icons |
| 500 | `#838EA3` | Tertiary/decorative text only (3.3:1 on white — below AA) |
| 600 | `#657089` | **AA floor** for meaningful text on white (5.0:1) |
| 700 | `#46516B` | Secondary text, prose body |
| 800 | `#26314A` | Strong secondary text |
| 900 | `#161F35` | Headings (near-ink) |
| 950 | `#0E1526` | Night Ops Navy anchor |

## 3. Functional Ramps

All ramps live as Tailwind v4 tokens in `assets/app.css` (`@theme`). Anchors in bold.

### Brand (navy) — `brand-*`
`50 #F0F4FA · 100 #DEE7F2 · 200 #C3D2E6 · 300 #9BB2D1 · 400 #6E8AB4 · 500 #4C6A99 · 600 #35517D · 700 #263E63 · 800 #1A2C4A · 900 #131F36 · **950 #0E1526**`

Links (`text-brand-600`), tinted chips (`bg-brand-50 text-brand-700`),
progress bars, logo tile, focus rings, dark chrome.

### Transmit Teal — `teal-*` (and `green-*` mirror)
`50 #EDFAF7 · 100 #D3F3EC · 200 #A9E7DB · 300 #74D4C4 · 400 #43BFAC · **500 #1FAF9C** · 600 #178F80 · 700 #12766A · 800 #115E55 · 900 #114D47 · 950 #062E2A`

WhatsApp channel, delivered/read states, success. **teal-500 fails AA under
white text (2.7:1)** — use teal-700+ behind white; teal-500 is for fills,
icons, and large UI shapes only.

### Signal Amber — `amber-*`
`50 #FFF8EB · 100 #FEEEC7 · 200 #FDDE8A · 300 #FFCA4D · **400 #FFB020** · 500 #F5A312 · 600 #E0930A · 700 #B26F06 · 800 #8C5508 · 900 #74450C · 950 #452605`

Email channel, primary CTAs, notifications. **Never white text on amber**
(1.9:1) — always Ink `#12182B` (9.3:1 ✓).

### Alert Coral — `coral-*` (and `red-*` mirror)
`50 #FDF1EF · 100 #FBDED9 · 200 #F7BFB5 · 300 #F1957F · 400 #EF8069 · **500 #E8604A** · 600 #CC4530 · 700 #A93A28 · 800 #8B3324 · 900 #732E22 · 950 #3F150D`

Errors and destructive actions only. White text needs coral-600+ (4.7:1 ✓).

### Info Blue — `blue-*`
`50 #EFF4FB · 100 #DEE9F7 · 200 #BFD3EF · 300 #94B4E3 · **400 #5E8BD9** · 500 #3D6ECC · 600 #2F58AB · 700 #274687 · 800 #223A6B · 900 #1D2F52 · 950 #131E36`

Informational states and the automation/"rules" accent on the landing page.
Professional, not part of the brand identity.

## 4. Semantic Mapping

| Semantic | Token / class | Values |
|---|---|---|
| Success | `badge-success`, `green-*` | teal ramp |
| Warning | `badge-warning`, `amber-*` | amber-50 tint + amber-700 text |
| Error | `badge-danger`, `red-*` | coral ramp |
| Info | `badge-info`, `blue-*` | info blue |
| WhatsApp channel | `--color-whatsapp` | `#1FAF9C` |
| Email channel | `--color-email` | `#FFB020` |
| Ink text | `--color-ink`, `text-ink` | `#12182B` |

## 5. Light Theme (authenticated app)

| Surface | Token | Hex |
|---|---|---|
| Canvas | `--color-canvas` | `#F3F5F7` (Paper) |
| Card / surface | `--color-surface` | `#FFFFFF` |
| Muted surface | `--color-surface-muted` | `#FAFBFC` |
| Border | `--color-border` | `#E8EDF3` |
| Border strong | `--color-border-strong` | `#DCE1E8` |
| Body text | `--color-ink` | `#12182B` |
| Secondary text | gray-700 | `#46516B` |
| Shadows | `--shadow-card/pop/modal` | rgb(14 21 38 / …) navy-tinted |

## 6. Dark Theme (landing / consoles)

Defined in `templates/accounts/landing.html` `:root`:

| Var | Hex | Role |
|---|---|---|
| `--ink` | `#0E1526` | Page background (Night Ops Navy) |
| `--ink-2` | `#161F35` | Raised panels |
| `--ink-3` | `#1C2640` | Console rows, hover step |
| `--hairline` | `#26314A` | Borders |
| `--text-hi / mid / lo` | `#FAFBFC / #A8B2C4 / #838EA3` | Text hierarchy |
| `--signal` | `#1FAF9C` | WhatsApp accent (+ glow rgba(31,175,156,.35)) |
| `--mail` | `#FFB020` | Email accent (+ glow rgba(255,176,32,.28)) |
| `--rules` | `#5E8BD9` | Automation accent (+ glow rgba(94,139,217,.28)) |

## 7. Buttons

| Variant | Default | Hover | Active | Text | Contrast |
|---|---|---|---|---|---|
| **Primary** `.btn-primary` | amber-400 `#FFB020` | amber-500 `#F5A312` | amber-600 `#E0930A` | Ink `#12182B`, semibold | 9.3:1 ✓ |
| **Secondary** `.btn-secondary` | white + border-300 | gray-50 | — | gray-700 | ✓ |
| **Ghost** `.btn-ghost` | transparent | gray-100 | — | gray-600→900 | ✓ |
| **Danger** `.btn-danger` | red-600 `#CC4530` | red-700 `#A93A28` | red-800 `#8B3324` | white | 4.7:1 ✓ |

- **Focus:** global 2 px navy outline (`--color-brand-600`, offset 2 px).
- **Disabled:** 60 % opacity + pointer-events none (from `.btn`).
- Dark surfaces: primary CTA stays amber-400/Ink (Ink on amber holds AA on any background).

## 8. Forms

- Labels: gray-700, medium.
- Inputs: white, border-300, text ink, placeholder gray-400.
- Focus: 2 px ring brand-500 `#4C6A99` (navy — amber rings would read as warnings).
- Error: border coral-400, bg coral-50, ring coral-500; message text red-600 `#CC4530`.
- Help text: gray-400 (decorative); use gray-600 if the hint is essential.

## 9. Status Badges

Tint background + dark text (never solid fills — solid is reserved for buttons):

| Badge | Classes | Colors |
|---|---|---|
| Neutral | `badge-neutral` | gray-100 / gray-600 |
| Brand | `badge-brand` | brand-50 / brand-700 |
| Success | `badge-success` | teal-50 / teal-800 |
| Warning | `badge-warning` | amber-50 / amber-700 `#B26F06` |
| Danger | `badge-danger` | coral-50 / coral-700 |
| Info | `badge-info` | blue-50 / blue-700 |

## 10. Charts & Analytics

Ordered categorical palette:

1. Teal `#1FAF9C` 2. Navy `#35517D` 3. Amber `#FFB020` 4. Info blue `#5E8BD9` 5. Neutral `#A8B2C4` 6. Deep teal `#115E55`

- **Coral `#E8604A` is reserved** for negative/error series (bounces, failures).
- Sequential scale: teal-100 → teal-800.
- Diverging scale: coral-500 ↔ neutral-200 ↔ teal-600.
- Gridlines `#E8EDF3`, axis labels `#657089`, tooltips: white card + border-200.

## 11. Dashboard Surfaces

Canvas Paper `#F3F5F7` → white cards (`border #E8EDF3`, `shadow-card`) →
muted wells `#FAFBFC`. Tables: `#FAFBFC` header band, gray-600 header text,
`divide-gray-100` rows. Skeletons: `gray-200/70` pulse.

## 12. Hero & Gradients

- Hero/base: linear navy-950 `#0E1526` → navy-900 `#131F36` (or `#161F35`).
- Atmosphere: radial glows only — teal rgba(31,175,156,≤.35) and amber
  rgba(255,176,32,≤.28), blurred ~90 px, opacity ≤ .25.
- **No purple, rainbow, or blue-purple SaaS gradients. Ever.**

## 13. CSS Variables

The canonical token source is the `@theme` block in [`assets/app.css`](../assets/app.css).
Every token is exposed both as a Tailwind utility (`bg-amber-400`, `text-ink`)
and a CSS variable (`var(--color-amber-400)`).

## 14. Tailwind Configuration

Tailwind **v4** — there is no `tailwind.config.js`. Configuration lives inline
in `assets/app.css` via `@theme` (tokens) and `@source` (content scanning).
The Akilent ramps *override* Tailwind's default `gray/green/red/amber/blue`
palettes so legacy utilities migrate automatically.

Rebuild after any token or template change:

```
tools/tailwindcss.exe -i assets/app.css -o static/css/app.css --minify
```

The compiled `static/css/app.css` is committed — don't forget the rebuild.

## 15. Figma Color Styles

Mirror tokens 1:1 using this naming:

```
Akilent/Brand/Navy/50 … 950
Akilent/Neutral/50 … 950
Akilent/Teal/50 … 950
Akilent/Amber/50 … 950
Akilent/Coral/50 … 950
Akilent/Blue/50 … 950
Akilent/Semantic/Ink · Success · Warning · Error · Info
Akilent/Channel/WhatsApp · Email
Akilent/Surface/Canvas · Card · Muted · Border · Border-Strong
Akilent/Dark/Ink · Ink-2 · Ink-3 · Hairline · Text-Hi · Text-Mid · Text-Lo
```

## 16. Usage Guidelines

- Neutrals first. Reach for an accent only when it carries meaning (channel, status, action).
- One primary (amber) CTA per view. Everything else is secondary/ghost.
- Teal always means WhatsApp/success; amber always means email/primary action; coral always means something is wrong.
- gray-600 is the minimum for text a user must read; gray-500/400 are decorative only.
- Dark surfaces use the navy ink scale — never `#000`.

## 17. Do's and Don'ts

**Do**
- Ink text on amber buttons (9.3:1)
- teal-700+ behind white text; teal-500 for fills/icons
- coral-600+ behind white text
- Tint + dark-text badges; solid fills for buttons only
- Radial teal/amber glows at low alpha for atmosphere

**Don't**
- White text on amber (1.9:1) or on teal-500 (2.7:1)
- Coral for decoration, emphasis, or "look at me" UI
- Purple/rainbow gradients or neon accents
- Amber badges styled like amber buttons (keep tint vs solid distinct)
- Pure black backgrounds or Tailwind stock grays

## 18. Accessibility Reference (WCAG AA)

| Pair | Ratio | |
|---|---|---|
| Ink `#12182B` on Amber `#FFB020` | 9.3:1 | ✓ text |
| White on coral-600 `#CC4530` | 4.7:1 | ✓ text |
| White on teal-700 `#12766A` | 5.4:1 | ✓ text |
| White on brand-600 `#35517D` | 9.0:1 | ✓ text |
| Paper on navy-950 | ~16:1 | ✓ text |
| gray-600 on white | 5.0:1 | ✓ text (AA floor) |
| gray-500 on white | 3.3:1 | ✗ decorative only |
| White on amber-400 | 1.9:1 | ✗ never |
| White on teal-500 | 2.7:1 | ✗ never (3:1 non-text ✓) |
