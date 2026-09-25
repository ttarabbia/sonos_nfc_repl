# Sonos NFC REPL

The REPL accepts commands from the terminal, NFC reader, and a small FastHTML
control page. All sources place work on the same command queue, so Sonos calls
are serialized.

```sh
uv run repl.py
```

It uses the existing mount at
`/tmp/jellyfin_mount/ttarabbia@gmail.com/dockerbox/jellyfin` by default. Set
`SONOS_NFC_MEDIA_ROOT` only if the media is mounted somewhere else.

The web controls listen on `0.0.0.0:8000` by default. Configure a specific
address or port with `SONOS_NFC_WEB_HOST` and `SONOS_NFC_WEB_PORT`; use the
machine's Tailscale address (or Tailscale Serve) to expose it only to the
tailnet. `SONOS_NFC_SPEAKER_HOST` overrides the direct Sonos fallback address.

The page uses ordinary HTML forms. Its only JavaScript updates and submits the
volume range slider when the slider is released.
