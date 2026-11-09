# Tunnel Studio

This source snapshot matches the application code and installer build scripts
shipped with Tunnel Studio 0.23.5 for Windows. The application version is in
`app/release.py`. Release notes are in `packaging/RELEASE_NOTES.txt`.

Windows Setup SHA-256

```text
90b642020bc09bf274248fd7e07e5f2bbcc209c48f59679c8ba802120e7553ce
```

The setup build also uses a bundled Windows runtime, generated payload lists and
installer artwork. Those packaging inputs are distributed separately from this
source snapshot. Application dependency pins are in `app/requirements.txt`
and `app/requirements-desktop.txt`.
