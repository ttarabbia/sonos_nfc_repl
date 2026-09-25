"""Sonos/NFC REPL with a small FastHTML control page.

Run with ``uv run repl.py``.  Terminal input, NFC scans, and HTTP form
submissions all enqueue commands; the single command worker is the only code
that talks to the Sonos speaker.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Optional
from urllib.parse import unquote, urlparse

import nfc
import usb.core
import usb.util
from fasthtml.common import (
    Button,
    Div,
    Form,
    H1,
    H2,
    Input,
    Label,
    Main,
    Meta,
    Option,
    P,
    Script,
    Select,
    Small,
    Span,
    Style,
    Title,
    fast_app,
)
from soco import SoCo, discovery
from soco.plugins.sharelink import ShareLinkPlugin
from starlette.requests import Request
from starlette.responses import RedirectResponse
from uvicorn import Config, Server


NFC_DEVICE_PATH = os.environ.get("SONOS_NFC_NFC_DEVICE", "usb:072f:2200")
NFC_USB_VENDOR_ID = 0x072F
NFC_USB_PRODUCT_ID = 0x2200
# The current WebDAV mount used by this machine. Override it if remounted.
MEDIA_MOUNT_POINT = Path(os.environ.get("SONOS_NFC_MEDIA_MOUNT", "/tmp/jellyfin_mount")).expanduser()
MEDIA_ROOT = Path(
    os.environ.get(
        "SONOS_NFC_MEDIA_ROOT",
        str(MEDIA_MOUNT_POINT / "ttarabbia@gmail.com/dockerbox/jellyfin"),
    )
).expanduser()
MEDIA_URI_PREFIX = "media:"
NFC_USB_INTERFACE = 1
NFC_RECONNECT_INITIAL_DELAY = 1
NFC_RECONNECT_MAX_DELAY = 30
ACR122U_LED_RED = bytes.fromhex("FF0040050400000000")
WEB_HOST = os.environ.get("SONOS_NFC_WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("SONOS_NFC_WEB_PORT", "8000"))
VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}


@dataclass(frozen=True)
class Command:
    action: str
    value: Optional[str] = None
    source: str = "terminal"


class VideoPlayer:
    """Start local videos without blocking the shared command worker."""

    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()

    def play(self, file_path: Path) -> None:
        display_on()
        with self.lock:
            previous = self.process
            if previous and previous.poll() is None:
                previous.terminate()
            self.process = subprocess.Popen(
                ["mpv", str(file_path), "--fullscreen", "--volume=80", "--really-quiet", "--keep-open=no"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            process = self.process
        print(f"Playing video: {file_path}")
        threading.Thread(target=self._wait_for_video, args=(process,), daemon=True).start()

    def _wait_for_video(self, process: subprocess.Popen) -> None:
        process.wait()
        with self.lock:
            if self.process is not process:
                return
            self.process = None
        display_off()

    def stop(self) -> None:
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
            self.process = None


def display_on() -> None:
    try:
        subprocess.run(["pmset", "displaysleepnow"], check=True)
        time.sleep(1)
        subprocess.run(["caffeinate", "-u", "-t", "5"], check=True, timeout=15)
        subprocess.Popen(["caffeinate", "-d"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("Display turned on")
    except Exception as exc:
        print(f"Could not turn display on: {exc}")


def display_off() -> None:
    try:
        subprocess.run(["pmset", "displaysleepnow"], check=True)
        print("Display turned off")
    except Exception as exc:
        print(f"Could not turn display off: {exc}")


def resolve_video_path(value: str, media_root: Path = MEDIA_ROOT) -> Optional[Path]:
    """Return a local video below ``media_root``, rejecting path traversal."""
    raw_path = unquote(value)
    if raw_path.startswith(MEDIA_URI_PREFIX):
        raw_path = raw_path.removeprefix(MEDIA_URI_PREFIX)
    if raw_path.startswith("file://"):
        raw_path = urlparse(raw_path).path

    root = media_root.expanduser().resolve()
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        candidate = candidate.resolve()
        candidate.relative_to(root)
    except ValueError:
        return None

    if not candidate.is_file() or candidate.suffix.lower() not in VIDEO_EXTENSIONS:
        return None
    return candidate


def available_videos(media_root: Path = MEDIA_ROOT) -> list[Path]:
    """Find playable videos below the mounted media directory."""
    root = media_root.expanduser().resolve()
    if not root.is_dir():
        return []
    try:
        return sorted(
            (
                path.resolve()
                for path in root.rglob("*")
                if path.is_file() and resolve_video_path(str(path), root) is not None
            ),
            key=lambda path: str(path).lower(),
        )
    except OSError as exc:
        print(f"Could not scan media root {root}: {exc}")
        return []


class CommandStack:
    """Owns command execution and the small state rendered by the web page."""

    def __init__(self, media_root: Path = MEDIA_ROOT, start_worker: bool = True):
        self.media_root = media_root
        self.commands: Queue[Command] = Queue()
        self.speaker = None
        self.sharelink = None
        self.player = VideoPlayer()
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.state_lock = threading.Lock()
        self.volume: Optional[int] = None
        self.last_result = "Waiting for a Sonos speaker."
        self.worker: Optional[threading.Thread] = None
        if start_worker:
            self.worker = threading.Thread(target=self._run, name="sonos-command-worker", daemon=True)
            self.worker.start()

    def set_speaker(self, speaker) -> None:
        self.speaker = speaker
        self.sharelink = ShareLinkPlugin(speaker)
        try:
            self.volume = int(speaker.volume)
        except Exception:
            self.volume = None
        self.ready.set()
        self._set_result("Connected to Sonos.")

    def enqueue(self, action: str, value: Optional[str] = None, source: str = "terminal") -> None:
        command = Command(action=action, value=value, source=source)
        self.commands.put(command)
        print(f"Queued from {source}: {action}{f' {value}' if value else ''}")

    def snapshot(self) -> dict[str, object]:
        with self.state_lock:
            return {
                "connected": self.ready.is_set(),
                "volume": self.volume if self.volume is not None else 23,
                "last_result": self.last_result,
                "queued": self.commands.qsize(),
            }

    def _set_result(self, result: str) -> None:
        with self.state_lock:
            self.last_result = result
        print(result)

    def _run(self) -> None:
        while not self.stopping.is_set():
            if not self.ready.wait(timeout=0.25):
                continue
            try:
                command = self.commands.get(timeout=0.25)
            except Empty:
                continue
            try:
                self.execute(command)
            except Exception as exc:
                self._set_result(f"{command.action} failed: {exc}")
            finally:
                self.commands.task_done()

    def execute(self, command: Command) -> None:
        """Execute one command. Kept public to make the queue behavior testable."""
        if self.speaker is None:
            raise RuntimeError("Sonos speaker is not connected")

        if command.action == "play":
            self.speaker.play()
            self._set_result("Playback resumed.")
        elif command.action == "pause":
            self.speaker.pause()
            self._set_result("Playback paused.")
        elif command.action == "next":
            self.speaker.next()
            self._set_result("Skipped to next track.")
        elif command.action == "volume":
            try:
                volume = int(command.value or "")
            except ValueError as exc:
                raise ValueError("Volume must be an integer from 0 to 100") from exc
            if not 0 <= volume <= 100:
                raise ValueError("Volume must be from 0 to 100")
            self.speaker.volume = volume
            with self.state_lock:
                self.volume = volume
            self._set_result(f"Volume set to {volume}.")
        elif command.action == "uri":
            self._play_uri(command.value or "")
        elif command.action == "video":
            self._play_video(command.value or "")
        elif command.action == "queue":
            titles = [item.title for item in self.speaker.get_queue()]
            self._set_result("Queue: " + (", ".join(titles) if titles else "empty"))
        else:
            raise ValueError(f"Unknown command: {command.action}")

    def _play_uri(self, uri: str) -> None:
        if "spotify" in uri:
            self.player.stop()
            self.speaker.avTransport.SetAVTransportURI([
                ("InstanceID", 0),
                ("CurrentURI", f"x-rincon-queue:{self.speaker.uid}#0"),
                ("CurrentURIMetaData", ""),
            ])
            self.sharelink.add_share_link_to_queue(uri, position=1, as_next=True)
            self.speaker.play_from_queue(0)
            self.speaker.shuffle = "playlist" in uri
            self._set_result(f"Started Spotify URI: {uri}")
            return
        self._play_video(uri)

    def _play_video(self, value: str) -> None:
        path = resolve_video_path(value, self.media_root)
        if path is None:
            raise ValueError("Video must be an existing video inside SONOS_NFC_MEDIA_ROOT")
        self.player.play(path)
        self._set_result(f"Started video: {path.relative_to(self.media_root.expanduser().resolve())}")

    def stop(self) -> None:
        self.stopping.set()
        self.player.stop()


def prepare_nfc_reader():
    """Run the known-working ACR122U interface-1 setup sequence."""
    device = usb.core.find(idVendor=NFC_USB_VENDOR_ID, idProduct=NFC_USB_PRODUCT_ID)
    if device is None:
        raise OSError("ACR122U reader not found")
    driver_detached = False
    try:
        device.detach_kernel_driver(NFC_USB_INTERFACE)
        driver_detached = True
        print("Detached the ACR122U CCID driver.")
        try:
            device.set_configuration()
            print("Configured the ACR122U.")
        except Exception as error:
            print(f"Could not configure the ACR122U: {error}")
    except Exception as error:
        # If it is already detached, nfcpy may still successfully claim it.
        print(f"Could not detach the ACR122U CCID driver: {error}")
    return device, driver_detached


def release_nfc_reader(device, driver_detached) -> None:
    """Return the CCID interface to macOS after nfcpy closes its handle."""
    if device is None:
        return
    try:
        if driver_detached:
            device.attach_kernel_driver(NFC_USB_INTERFACE)
            print("Reattached the ACR122U CCID driver.")
    except Exception as error:
        print(f"Could not reattach the ACR122U CCID driver: {error}")
    finally:
        usb.util.dispose_resources(device)


def close_nfc_frontend_for_restart(frontend) -> None:
    """Release libusb without nfcpy's ACR122U power-down command.

    The normal close path leaves this reader unable to reopen on this Mac.
    Releasing the transport directly retains the reader state required for the
    next REPL startup.
    """
    device = frontend.device
    if device is None:
        return
    transport = device.chipset.transport
    try:
        device.chipset.ccid_xfr_block(ACR122U_LED_RED)
        print("NFC listener stopped; reader LED is red.")
    except (IOError, OSError) as error:
        print(f"Could not set the reader LED to red: {error}")
    finally:
        try:
            if transport is not None:
                transport.close()
        finally:
            device.chipset.transport = None
            frontend.device = None


def handle_nfc_tag(tag, commands: CommandStack) -> bool:
    """Extract a tag URI and put its commands on the shared stack."""
    try:
        if not hasattr(tag, "ndef") or not tag.ndef:
            return True
        for record in tag.ndef.records:
            uri = getattr(record, "uri", None)
            if not uri:
                continue
            commands.enqueue("volume", "23", source="nfc")
            commands.enqueue("uri", uri, source="nfc")
            print(f"NFC tag queued: {uri}")
            return True
    except Exception as exc:
        print(f"Error handling NFC tag: {exc}")
    return True


def nfc_listener(commands: CommandStack) -> None:
    """Poll the reader, reconnecting with the known-working ACR122U sequence."""
    reconnect_delay = NFC_RECONNECT_INITIAL_DELAY
    while not commands.stopping.is_set():
        reader_device = None
        driver_detached = False
        try:
            reader_device, driver_detached = prepare_nfc_reader()
            frontend = nfc.ContactlessFrontend(NFC_DEVICE_PATH)
            try:
                print("NFC listener started. Waiting for tags...")
                reconnect_delay = NFC_RECONNECT_INITIAL_DELAY
                while not commands.stopping.is_set():
                    target = frontend.sense(nfc.clf.RemoteTarget("106A"), iterations=10, interval=0.1)
                    if target is None:
                        continue
                    tag = nfc.tag.activate(frontend, target)
                    if tag:
                        handle_nfc_tag(tag, commands)
                        chipset = frontend.device.chipset
                        chipset.set_buzzer_and_led_to_active(duration_in_ms=100)
                        chipset.send_ack()
                        chipset.set_buzzer_and_led_to_default()
                        commands.stopping.wait(12)  # Avoid processing the same card repeatedly.
            finally:
                close_nfc_frontend_for_restart(frontend)
        except (IOError, OSError) as error:
            if commands.stopping.is_set():
                break
            print(f"NFC reader error: {error}. Reconnecting in {reconnect_delay} second(s)...")
            commands.stopping.wait(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, NFC_RECONNECT_MAX_DELAY)
        except Exception as error:
            if commands.stopping.is_set():
                break
            print(f"Unexpected NFC error: {error}. Reconnecting in {reconnect_delay} second(s)...")
            commands.stopping.wait(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, NFC_RECONNECT_MAX_DELAY)
        finally:
            release_nfc_reader(reader_device, driver_detached)


def page(controller: CommandStack, message: Optional[str] = None):
    """Render an ordinary HTML control page; no client framework is used."""
    state = controller.snapshot()
    videos = available_videos(controller.media_root)
    root = controller.media_root.expanduser().resolve()
    video_picker = (
        Form(
            Label("Video", _for="video"),
            Select(
                *[Option(str(video.relative_to(root)), value=str(video.relative_to(root))) for video in videos],
                id="video",
                name="video",
                required=True,
            ),
            Button("Play video", type="submit"),
            method="post",
            action="/video",
        )
        if videos
        else P(f"No videos found under {root}. Mount it or set SONOS_NFC_MEDIA_ROOT.")
    )
    return (
        Title("Sonos NFC Remote"),
        Main(
            H1("Sonos NFC Remote"),
            P("Connected" if state["connected"] else "Waiting for Sonos discovery…"),
            P(message or state["last_result"], id="status"),
            Small(f'{state["queued"]} command(s) waiting'),
            H2("Playback"),
            Div(
                Form(Button("Play", type="submit"), method="post", action="/command/play", cls="play"),
                Form(Button("Pause", type="submit"), method="post", action="/command/pause"),
                Form(Button("Next", type="submit"), method="post", action="/command/next"),
                cls="controls",
            ),
            H2("Volume"),
            Form(
                Label("Volume: ", Span(str(state["volume"]), id="volume-value"), _for="volume"),
                Input(
                    type="range",
                    id="volume",
                    name="volume",
                    min="0",
                    max="100",
                    value=str(state["volume"]),
                    data_volume_slider=True,
                ),
                method="post",
                action="/volume",
            ),
            H2("Videos"),
            video_picker,
            P("This page is intended to be reached through your Tailscale network."),
            Style("""
                :root { color-scheme: light dark; font: 18px/1.4 system-ui, sans-serif; }
                body { margin: 0; background: Canvas; color: CanvasText; }
                main {
                    box-sizing: border-box; max-width: 38rem; min-height: 100dvh; margin: 0 auto;
                    padding: max(1rem, env(safe-area-inset-top)) 1rem
                        max(1.5rem, env(safe-area-inset-bottom));
                }
                h1 { margin: 0 0 .25rem; font-size: 1.65rem; }
                h2 { margin: 1.75rem 0 .5rem; font-size: 1.2rem; }
                #status { min-height: 2.8rem; margin: .5rem 0 .1rem; color: color-mix(in srgb, CanvasText 78%, Canvas); }
                small { color: color-mix(in srgb, CanvasText 66%, Canvas); }
                .controls { display: grid; grid-template-columns: 1fr 1fr; gap: .75rem; }
                .controls form { margin: 0; } .controls .play { grid-column: 1 / -1; }
                button, select, input[type=range] { box-sizing: border-box; width: 100%; font: inherit; }
                button, select { min-height: 3.4rem; border-radius: .7rem; }
                button { border: 0; background: #2563eb; color: white; font-weight: 700; }
                button:active { background: #1d4ed8; transform: scale(.98); }
                form { margin: .85rem 0; } label { display: block; font-weight: 650; }
                select { display: block; margin-top: .45rem; padding: .65rem; border: 1px solid #7c7c7c; background: Canvas; color: CanvasText; }
                input[type=range] { display: block; height: 3.2rem; margin-top: .25rem; accent-color: #2563eb; }
            """),
            # The only JavaScript updates the visible value while the range is dragged.
            Script("""
                const slider = document.querySelector('[data-volume-slider]');
                const value = document.querySelector('#volume-value');
                slider.addEventListener('input', () => value.textContent = slider.value);
                slider.addEventListener('change', () => slider.form.submit());
            """),
        ),
    )


def create_app(controller: CommandStack):
    # This app has no sessions; supplying a key avoids FastHTML writing a .sesskey file.
    app, route = fast_app(
        title="Sonos NFC Remote",
        hdrs=(Meta(name="viewport", content="width=device-width, initial-scale=1, viewport-fit=cover"),),
        default_hdrs=False,
        htmx=False,
        surreal=False,
        secret_key=os.environ.get("SONOS_NFC_SESSION_SECRET", "no-session-needed"),
    )

    @route("/")
    def index(request: Request):
        return page(controller, request.query_params.get("message"))

    @route("/command/{action}", methods=["POST"])
    def post_command(action: str):
        if action not in {"play", "pause", "next"}:
            return RedirectResponse("/?message=Unknown+command", status_code=303)
        controller.enqueue(action, source="web")
        return RedirectResponse(f"/?message={action.title()}+queued", status_code=303)

    @route("/volume", methods=["POST"])
    async def post_volume(request: Request):
        form = await request.form()
        volume = str(form.get("volume", ""))
        try:
            if not 0 <= int(volume) <= 100:
                raise ValueError
        except ValueError:
            return RedirectResponse("/?message=Volume+must+be+0-100", status_code=303)
        controller.enqueue("volume", volume, source="web")
        return RedirectResponse(f"/?message=Volume+{volume}+queued", status_code=303)

    @route("/video", methods=["POST"])
    async def post_video(request: Request):
        form = await request.form()
        video = str(form.get("video", ""))
        if resolve_video_path(video, controller.media_root) is None:
            return RedirectResponse("/?message=Invalid+video", status_code=303)
        controller.enqueue("video", video, source="web")
        return RedirectResponse("/?message=Video+queued", status_code=303)

    return app


def discover_speaker(commands: CommandStack) -> None:
    configured_host = os.environ.get("SONOS_NFC_SPEAKER_HOST", "192.168.0.103")
    while not commands.stopping.is_set():
        try:
            speaker = discovery.any_soco(allow_network_scan=True)
            if speaker is None and configured_host:
                speaker = SoCo(configured_host)
            if speaker is not None:
                commands.set_speaker(speaker)
                return
        except Exception as exc:
            print(f"Sonos discovery failed: {exc}")
        print("No Sonos speaker found; retrying in 1 second.")
        time.sleep(1)


def terminal_repl(commands: CommandStack) -> None:
    print("REPL started. Commands: play, pause, next, queue, -v VOLUME, -a URI, video PATH, exit")
    while True:
        try:
            parts = shlex.split(input(">> "))
        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            return
        if not parts:
            continue
        command, *arguments = parts
        if command == "exit":
            return
        if command in {"play", "pause", "next", "queue"} and not arguments:
            commands.enqueue(command)
        elif command == "-v" and len(arguments) == 1:
            commands.enqueue("volume", arguments[0])
        elif command == "-a" and len(arguments) == 1:
            commands.enqueue("uri", arguments[0])
        elif command == "video" and len(arguments) == 1:
            commands.enqueue("video", arguments[0])
        else:
            print("Invalid command. Commands: play, pause, next, queue, -v VOLUME, -a URI, video PATH, exit")


def main() -> None:
    commands = CommandStack()
    app = create_app(commands)
    web_server = Server(Config(app, host=WEB_HOST, port=WEB_PORT, log_level="info"))
    threading.Thread(target=web_server.run, name="web-server", daemon=True).start()
    threading.Thread(target=discover_speaker, args=(commands,), name="sonos-discovery", daemon=True).start()
    threading.Thread(target=nfc_listener, args=(commands,), name="nfc-listener", daemon=True).start()
    print(f"Web controls listening on http://{WEB_HOST}:{WEB_PORT}")
    try:
        terminal_repl(commands)
    finally:
        commands.stop()
        web_server.should_exit = True


if __name__ == "__main__":
    main()
