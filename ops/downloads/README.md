# Masha test-build download

Public test package:

- file: `masha-stage-1.8-windows-x64-e93bf0d15.zip`;
- source commit: `e93bf0d15a775e3d00504cff0fb89a3e1a321b83`;
- SHA-256: `DF86C4D9C970AB65DBA5DBB00852E63E414FFAC298EA5A0D43A1D124189820E1`;
- VPS directory: `/opt/masha-downloads`;
- service: `masha-download.service`;
- URL: `http://77.222.38.70/masha-stage-1.8-windows-x64-e93bf0d15.zip`.

The endpoint serves only the exact release filename and has no directory listing.
The download is plain HTTP because VPS port 443 is occupied by the Masha relay.
Verify SHA-256 after downloading.
