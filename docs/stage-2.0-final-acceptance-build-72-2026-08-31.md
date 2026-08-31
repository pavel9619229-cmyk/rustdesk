# Stage 2.0 final acceptance — build 72

Date: 2026-08-31.

## Final Windows x64 build

- Build: 72 (`1.4.9+72`).
- Binary source commit: `b28e77862`.
- Packaging/publication checkpoint: `e27fe54b7`.
- Toolchain: Flutter 3.24.5, Dart 3.5.4, Rust 1.75.0.
- Pipeline result: `BUILD=SUCCESS`.
- PE machine: `8664` (Windows x64).
- Final artifact: `artifacts/build-72-final-b28e77862/`.
- EXE SHA-256: `4DC36AEE5129DA3BA3412B371F0BFDB4ED07E87D8AB1594B364588E3454C9CD8`.
- ZIP SHA-256: `2B4121446EBAF35AFE167DB03A0CAACCAD349F686C75997D8A553241A0ABBE90`.

## Frontend acceptance

- The application displays `Сборка 72` in the upper-left corner from `PackageInfo.buildNumber`.
- Visual check on MSI confirmed the build label is separate from the AGPL attribution and does not overlap it.
- Billing panel displays tariff, debt, due time, grace state, warning text, access state and grant source.
- With operator `515663171` blocked for non-payment, build 72 displayed `Заблокировано` / `Нет действующего права`.

## Server acceptance

- User-confirmed real unpaid-session cutoff was recorded in `docs/stage-2.0-postpaid-blocking-incident-2026-08-31.md`.
- The obsolete `build-69-test-safety` admin grant was revoked; it no longer bypasses postpaid blocking.
- On build 72, a new connection attempt `515663171 -> 426031895` at 21:32:39 was denied by production with HTTP 403 and `payment_required`.
- Production remained healthy after publishing build 72; billing state remained `blocked` and debt data was not reset.
- The server test suite passed 30/30 after the final download whitelist change.

## Rejected intermediate artifact

The first build-72 artifact from `769693f14` was rejected because the build label overlapped the AGPL attribution. It is marked `REJECTED.txt` on `server` and is not present in the active download whitelist.

## Result

`STAGE_2_0=PASS`

Next stage: **2.1 YooKassa — create payment, receive verified webhook, settle debt, restore access.**
