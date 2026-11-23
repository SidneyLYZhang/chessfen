# Port the brand redesign into the server-rendered Jinja app

Status: accepted — implemented in #11–#19; the legacy stylesheet was removed once the last page migrated.

The owner approved a complete visual redesign, delivered as a React + Vite + Tailwind prototype covering every page. The production app is a FastAPI application with Jinja templates, form posts, gettext, and a single hand-written stylesheet; it has no Node build step, and the Docker image must stay small and CPU-light. The redesign therefore becomes a **port of the visual system into the existing server-rendered stack**, not an adoption of the prototype's implementation.

The port keeps every URL, form flow, localization path, and API contract. The prototype's custom CSS is translated into one token-driven stylesheet. Because the redesign touches every page, the stylesheet migration is sequenced as expand–contract: the legacy stylesheet is renamed and kept, every new rule is scoped to a `v2` body class so it stays inert, pages migrate one at a time, and the legacy stylesheet is deleted only after the last page moves. Client interactivity stays minimal: copy buttons and navigation enhancement only, with every main flow working without JavaScript.

## Considered Options

- **Adopt the React prototype as the production frontend**: would require rebuilding authentication, admin flows, gettext, and form handling around a new API surface, plus a Node build stage in CI and the image. Rejected: large blast radius, no user-visible benefit over a faithful port.
- **Hybrid (Jinja pages plus React for heavy widgets)**: introduces a second frontend stack and duplicated i18n and styling concerns. Rejected: complexity without a matching payoff.
- **Port into the server-rendered stack (chosen)**: highest design fidelity per unit of risk, keeps deployment and tests unchanged in shape, and preserves the no-JavaScript baseline.

## Consequences

- The design system is maintained by hand in CSS; there is no utility-class build. Tokens and shared components live in one stylesheet.
- The redesign ships page by page; during the migration the new stylesheet is scoped and inert, and the legacy stylesheet remains until the final contraction step.
- Styling is light-only. The former `prefers-color-scheme` dark variant is not carried into the new design and is removed when the new shell switches on, because the approved brand defines only light surfaces plus deliberate dark brand panels.
- Latin display and monospace faces are self-hosted under the SIL Open Font License; CJK text uses system families to keep the image and page weight low.
- The prototype's simulated behavior (demo credentials, fake recognition stages, scopes, quotas) is not implemented; only real backend capabilities are surfaced.
- Rendered board images adopt the brand palette in both web previews and API output. This is a visual change, not an API contract change.
- Any future move to a client-side application remains possible, but must start from a new ADR rather than accreting a second stack.