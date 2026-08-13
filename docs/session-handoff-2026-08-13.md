# Masha Remote Operator Session Handoff

## Repository

- Workspace: `C:\Users\Server\Documents\UDU`
- Repository and branch: `pavel9619229-cmyk/rustdesk`, `udu/1.4.9`
- Latest committed fix: `1801110647ac79390f1e572316c34fc7234cf8a6` (`fix: correct Masha server public key`)
- Do not revert or commit the large pre-existing user Flutter worktree changes.

## VPS And RustDesk Server

- SpaceWeb VPS, Ubuntu 24.04, public IPv4: `77.222.38.70`.
- Docker containers use host networking and `/opt/masha-rustdesk:/root`:
  - `hbbs`: `rustdesk/rustdesk-server hbbs -r 77.222.38.70:21117`
  - `hbbr`: `rustdesk/rustdesk-server hbbr`
- UFW permits `21115/tcp`, `21116/tcp`, `21116/udp`, `21117/tcp`, and `21118/tcp`.
- The active public server key, read from `id_ed25519.pub`, is:

  ```text
  9ceMofgvYTVTIC95mhjgmTejoqprML2iaMONVQJo8I=
  ```

- SSH from this VS Code network reaches port 22 but times out during SSH banner exchange. Use SpaceWeb noVNC for server administration.
- noVNC text input mangles `_`, `:`, `?`, and `*`; send such characters as explicit key presses.

## Client Configuration

`src/common.rs` now embeds:

```rust
pub const MASHA_RENDEZVOUS_SERVER: &str = "77.222.38.70";
pub const MASHA_SERVER_PUBLIC_KEY: &str = "9ceMofgvYTVTIC95mhjgmTejoqprML2iaMONVQJo8I=";
```

The prior build had a typo in this key: it omitted the `2` in `...qprML2ia...`.

`common::get_key()` uses this precedence on Windows:

1. Custom key encoded in executable name, if present.
2. Persisted manual `key` setting.
3. Embedded `MASHA_SERVER_PUBLIC_KEY`.

## Current Connectivity Evidence

- Initial client logs reported `Handshake failed: invalid public key from rendezvous server`, caused by the typo above.
- After manual correction, the UI reports `Key mismatch` / `Несоответствие ключей`.
- Source inspection confirms `src/client.rs` maps the server's `LICENSE_MISMATCH` punch response to this UI error. It means `hbbs` received different effective `licence_key` values from the two peers. It is not a relay failure and is not a cached peer fingerprint error.
- Initiating PC ID: `294 098 875`.
- Target/client PC ID: `114 983 435`.
- A search in `%APPDATA%\masha-remote-operator\config\peers` for `114983435` returned no file. Do not advise deleting a peer cache again.
- Both user-facing settings screens have reportedly been changed to the corrected key. Do not assert which PC is wrong without runtime or server evidence.
- Both clients reach `hbbs`; TCP and UDP captures must not be conflated. Investigate `hbbr` only after `LICENSE_MISMATCH` is resolved.

## Corrected Build

- GitHub Actions run: https://github.com/pavel9619229-cmyk/rustdesk/actions/runs/31645256429
- The workflow is marked failed only because `i686-pc-windows-msvc` failed.
- Windows x64 build succeeded and is ready:
  - Artifact: `masha-remote-operator-windows-x86_64`
  - Artifact ID: `9161420522`
  - Size: `33,966,629` bytes
  - Digest: `sha256:b921b0f2a43614aa9f8d468163ae73034fbc8ecc62190b03665a9b3eb102846a`
- Universal Android artifact also succeeded:
  - Artifact: `masha-remote-operator-android-universal`
  - Artifact ID: `9161524803`
  - Size: `71,484,270` bytes
- User preference: mobile build delivery only through Telegram.

## Next Steps

1. Replace both Windows test clients with the Windows x64 artifact from commit `180111064`; leave all manual server/key fields empty so embedded values are used.
2. Make one connection attempt and inspect `hbbs`/`hbbr` logs. If `LICENSE_MISMATCH` persists, instrument or inspect the `hbbs` version to log fingerprints of both received `licence_key` values before assigning blame to either machine.
3. After resolving the key mismatch, capture actual relay traffic on TCP `21117` during a single new attempt.

## Interaction Notes

- Give the user short, literal GUI instructions.
- Avoid repeated manual tests and do not present assumptions as facts.
- Do not repeatedly instruct the user how to dismiss the same error dialog.