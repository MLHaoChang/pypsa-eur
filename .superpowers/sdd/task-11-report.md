# Task 11 Report — Login + password pages (split brand layout)

## Status

Implemented real login, set-password, and reset-password pages under `pypsa-gui/frontend/src/pages/auth/` and replaced the inline placeholders from `src/routes.tsx`.

## Changes

- added a split-brand auth layout with a forest-green left panel and light-form right panel
- extracted `LoginPage`, `SetPasswordPage`, `ResetPasswordPage`, and shared `PasswordTokenPage` / layout primitives into `src/pages/auth/`
- added `src/auth/requests.ts` to keep login/forgot/set/reset submit flows pure and Vitest-friendly
- updated `AuthProvider.login()` to use the shared login helper so email trimming and login submission logic live in one place
- renamed the forgot-password API entry point to `authApi.forgotPassword` and kept `authApi.forgot` as a compatibility alias
- added `src/auth/requests.test.ts` covering login submission, forgot-password submission, and set/reset token validation + routing to the correct API call

## Verification

- `npm test` ✅ (45 tests passed)
- `npx tsc -b` ✅
- `npm run build` ❌ still fails with `Could not resolve entry module "index.html"`; same packaging/setup issue previously noted in Task 10

## Concerns

- no browser-based visual QA was run in this headless session, so the split layout was verified by code review plus TypeScript/build-path checks rather than an interactive render
