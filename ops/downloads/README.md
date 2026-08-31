# Masha test-build download

Public test package:

- file: `masha-stage-1.8-windows-x64-e93bf0d15.zip`;
- source commit: `e93bf0d15a775e3d00504cff0fb89a3e1a321b83`;
- SHA-256: `DF86C4D9C970AB65DBA5DBB00852E63E414FFAC298EA5A0D43A1D124189820E1`;
- VPS directory: `/opt/masha-downloads`;
- service: `masha-download.service`;
- URL: `http://77.222.38.70/masha-stage-1.8-windows-x64-e93bf0d15.zip`.

Stage 2.0 frontend acceptance package:

- build number: `69`;
- file: `masha-build-69-stage-2.0-windows-x64-63271488f.zip`;
- source commit: `63271488f5d27b00bcff272714acfe3134a48854`;
- SHA-256: `0B256931D01D77D958BD3FEE957A9CE982615026729103AFCE9CC2E48B32CB7C`;
- HTTP URL: `http://77.222.38.70/masha-build-69-stage-2.0-windows-x64-63271488f.zip`;
- VPS HTTPS fallback: `https://77.222.38.70:8443/downloads/masha-build-69-stage-2.0-windows-x64-63271488f.zip`;
- GitHub HTTPS fallback: `https://github.com/pavel9619229-cmyk/rustdesk/releases/download/masha-stage-2.0-frontend-63271488f/masha-build-69-stage-2.0-windows-x64-63271488f.zip`.

The endpoint serves only the exact release filenames and has no directory listing.
The legacy link uses plain HTTP because VPS port 443 is occupied by the Masha relay.
The fallback link is encrypted by the Masha HTTPS service on port 8443.
Verify SHA-256 after downloading.
