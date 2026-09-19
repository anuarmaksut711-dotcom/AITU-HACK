# Soyle

Frontend identity: **Soyle**. The visual direction pairs warm paper, forest green, and a dialogue-shaped S mark. The name is a working creative choice; domain and trademark availability were not checked.

The brand covers the sign-in screen, workspace navigation, page titles, browser icon, and shared Mantine palette. Runtime brand values live in `apps/web/src/brand.ts`; initial HTML metadata lives in `apps/web/index.html`.

The existing internal package names, API request marker, CSP nonce placeholder, database, and deployment identifiers retain `aimeet`.

## Logo source

- Generated with the built-in ImageGen tool on 2026-09-11.
- Original transparent PNG: `apps/web/src/assets/soyle-mark.png`.
- Used directly by the brand component and browser icon. Vite fingerprints the asset for caching.
- Keep the wordmark as live text for legibility and accessibility.

## Frontend verification

- TypeScript checks, Vite production build, ESLint, and whitespace checks passed.
- Checked sign-in, meeting list, creation, and detail views in an isolated browser at desktop and 320 px mobile widths. The generated logo loads, titles use Soyle, and no horizontal overflow was observed.
- Sign-in validation still focuses the invalid email field. Darkened validation text to retain contrast on the new warm background.
- Axe reported no violations on the final mobile sign-in validation state, creation form, and meeting detail view.
- Authenticated views used browser-only test responses. No live login, persistence, transcription, or model execution was tested as part of the rebrand.
- The development preview is at `http://127.0.0.1:5173/login`; the Docker-served frontend was not redeployed.

## Generation prompt

```text
Use case: logo-brand
Asset type: production app logo symbol for Soyle, a meeting transcript and conversation workspace. This image will be used directly at 32–56 pixels wide in a React web app and as the favicon.
Primary request: design ONE refined, distinctive compact abstract S symbol made from TWO interlocking rounded speech-like ribbons, suggesting a dialogue becoming one clear thread. The upper and lower forms have a clear generous negative-space separation. Balanced, simple, solid silhouette that remains readable at favicon size. Quiet, confident, contemporary editorial software identity.
Style/medium: flat vector-like logo artwork with precise smooth geometry, premium optical balance, no outlines, no texture.
Color palette: one solid deep forest green #215C49.
Scene/backdrop: actual transparent background (alpha), no simulated checkerboard.
Composition: square 1024x1024 PNG. The single symbol is centered and fills approximately 88% of the canvas height and width, with a small even safety margin. No surrounding tile or container. Both strokes have consistent generous thickness.
Text: NO TEXT, NO WORDMARK, NO LETTERING. Only the abstract S-like dialogue symbol.
Avoid: multiple variants, grid, presentation board, drop shadows, gradients, 3D, glossy lighting, watermark, thin lines, tiny details, microphone icons, AI sparkles, generic chat bubble outlines.
```
