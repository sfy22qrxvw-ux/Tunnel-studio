# Tunnel Studio

This source snapshot matches the application code and installer build scripts
shipped with Tunnel Studio 0.23.4 for Windows. The application version is in
`app/release.py`. Release notes are in `packaging/RELEASE_NOTES.txt`.

Windows Setup SHA-256

```text
9dc8f4d6af5777f665f82da5fe434b3fea3697e1f2a755181d411bcb0c7a350f
```

The setup build also uses a bundled Windows runtime, generated payload lists and
installer artwork. Those packaging inputs are distributed separately from this
source snapshot. Application dependency pins are in `app/requirements.txt`
and `app/requirements-desktop.txt`.
