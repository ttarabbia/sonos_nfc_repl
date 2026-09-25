from soco.plugins.sharelink import ShareLinkPlugin
from soco import SoCo, discovery
import sys
import threading
import time
import nfc
import os
import subprocess
import ndef
import usb.core
import usb.util
from pathlib import Path
from typing import Optional
from urllib.parse import unquote
from ndef.uri import UriRecord
from binascii import hexlify
sys.path.insert(0, "./vendor")

mpv_process: Optional[subprocess.Popen] = None
# Mount the WebDAV share here. /Volumes is macOS's conventional persistent
# mount location. Override either setting without changing code.
MEDIA_MOUNT_POINT = Path(os.environ.get(
    "SONOS_NFC_MEDIA_MOUNT",
    "/Volumes/Jellyfin",
)).expanduser()
MEDIA_ROOT = Path(os.environ.get(
    "SONOS_NFC_MEDIA_ROOT",
    str(MEDIA_MOUNT_POINT / "ttarabbia@gmail.com/dockerbox/jellyfin"),
)).expanduser()
# A card stores this prefix followed by a path relative to MEDIA_ROOT.
MEDIA_URI_PREFIX = "media:"
NFC_DEVICE_PATH = "usb:072f:2200"
NFC_USB_VENDOR_ID = 0x072F
NFC_USB_PRODUCT_ID = 0x2200
# This is the interface used by the last known-working reader setup
# (commits 89d6dad and 019bb3b). Do not change it to interface 0 without
# testing against the physical ACR122U.
NFC_USB_INTERFACE = 1
NFC_RECONNECT_INITIAL_DELAY = 1
NFC_RECONNECT_MAX_DELAY = 30
ACR122U_LED_RED = bytes.fromhex("FF0040050400000000")

def kill_video():
    global mpv_process
    if mpv_process and mpv_process.poll() is None:
        mpv_process.terminate()
        mpv_process.wait()
        mpv_process = None
        display_off()

def play_video_background(file_path):
    global mpv_process
    try:
        kill_video()
        display_on()
        cmd = ["mpv", file_path, "--fullscreen", "--volume=80", "--really-quiet", "--keep-open=no"]
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        mpv_process = proc
        print(f"Playing: {file_path}")

        def cleanup(p):
            p.wait()
            if mpv_process is p:
                mpv_process = None
                display_off()

        threading.Thread(target=cleanup, args=(proc,), daemon=True).start()
    except Exception as e:
        print(f"Failed to play: {e}")


def media_path_from_uri(uri):
    """Resolve a media: URI to a file below the configured mounted share."""
    if not uri.startswith(MEDIA_URI_PREFIX):
        return None

    relative_path = unquote(uri.removeprefix(MEDIA_URI_PREFIX))
    if not relative_path:
        print("Media tag has no path after 'media:'.")
        return None

    if not MEDIA_ROOT.is_dir():
        print(
            f"Media share is unavailable at {MEDIA_ROOT}. "
            "Mount it, or set SONOS_NFC_MEDIA_ROOT."
        )
        return None

    media_root = MEDIA_ROOT.resolve()
    media_path = (media_root / relative_path).resolve()
    try:
        media_path.relative_to(media_root)
    except ValueError:
        print("Media tag path must stay inside the configured media share.")
        return None

    if not media_path.is_file():
        print(f"Media file not found: {media_path}")
        return None

    return media_path


def play_media_uri(uri):
    """Start the locally mounted media file identified by a media: URI."""
    media_path = media_path_from_uri(uri)
    if media_path is None:
        return False

    play_video_background(str(media_path))
    return True

def display_on():
    try:
        subprocess.run(['pmset', 'displaysleepnow'], check=True)
        time.sleep(1)
        subprocess.run(['caffeinate', '-u', '-t', '5'], check=True, timeout=15)
        subprocess.Popen(['caffeinate', '-d'], 
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        print("Display turned on")
    except Exception as e:
        print(f"Failed: {e}")

def display_off():
    try:
        for i in range(5):
            subprocess.run(['pmset', 'displaysleepnow'], check=True)
            if i < 4:
                time.sleep(1)
        print("Display turned off")
    except Exception as e:
        print(f"Failed: {e}")

def play_uri(speaker, sharelink, uri):
    """Play a URI by adding it to the queue and starting playback."""
    # Exit TV/line-in mode by switching transport to the queue
    speaker.avTransport.SetAVTransportURI([
        ("InstanceID", 0),
        ("CurrentURI", f"x-rincon-queue:{speaker.uid}#0"),
        ("CurrentURIMetaData", ""),
    ])
    sharelink.add_share_link_to_queue(uri, position=1, as_next=True)
    speaker.play_from_queue(0)
    if 'playlist' in uri:
        speaker.shuffle = True
    else:
        speaker.shuffle = False
    print(f"Added {uri} to the queue and started playback.")
    return uri

def handle_nfc_tag(tag, speaker, sharelink):
    """Handle NFC tag detection and read the URI."""
    try: 
        if hasattr(tag, 'ndef') and tag.ndef:
            for record in tag.ndef.records:
                if hasattr(record, 'uri') and record.uri:
                    speaker.volume = int(23)
                    uri = record.uri
                    if uri.startswith(MEDIA_URI_PREFIX):
                        play_media_uri(uri)
                    elif 'spotify' in uri:
                        kill_video()
                        play_uri(speaker, sharelink, uri)
                    else:
                        print(f"Unsupported NFC URI: {uri}")
                    print(f"\n>> -a {uri}")
                    return True
        return True
    except Exception as e:
        print(f"error handling tag: {e}")
        return True


def prepare_nfc_reader():
    """Run the ACR122U setup sequence used by the known-working listener."""
    device = usb.core.find(
        idVendor=NFC_USB_VENDOR_ID,
        idProduct=NFC_USB_PRODUCT_ID,
    )
    if device is None:
        raise OSError("ACR122U reader not found")

    driver_detached = False
    try:
        # Do this unconditionally. The reader only became reliable after this
        # exact interface-1 handoff was introduced in the earlier working code.
        device.detach_kernel_driver(NFC_USB_INTERFACE)
        driver_detached = True
        print("Detached the ACR122U CCID driver.")
        try:
            device.set_configuration()
            print("Configured the ACR122U.")
        except Exception as error:
            print(f"Could not configure the ACR122U: {error}")
    except Exception as error:
        # This is expected if the driver is already detached. Continue and let
        # nfcpy attempt to claim the reader, as the working code did.
        print(f"Could not detach the ACR122U CCID driver: {error}")

    return device, driver_detached


def release_nfc_reader(device, driver_detached):
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


def close_nfc_frontend_for_restart(clf):
    """Release libusb without sending nfcpy's ACR122U power-down command.

    nfcpy's normal ACR122U close path turns both LEDs off and leaves this
    reader unable to be reopened on this Mac until it is physically replugged.
    Closing the transport releases the USB interface while retaining the
    reader state needed for the next REPL startup.
    """
    device = clf.device
    if device is None:
        return

    transport = device.chipset.transport
    try:
        # Set a persistent red-only state (no buzzer) while the REPL does not
        # own the reader. nfcpy initialization restores its normal green LED.
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
            clf.device = None


def nfc_listener(stop_event, speaker, sharelink):
    """Own the NFC reader until asked to stop, reconnecting after I/O errors."""
    reconnect_delay = NFC_RECONNECT_INITIAL_DELAY

    while not stop_event.is_set():
        reader_device = None
        driver_detached = False
        try:
            reader_device, driver_detached = prepare_nfc_reader()
            # nfcpy claims and releases the ACR122U interface itself. Do not
            # directly manipulate its USB interface after this handoff.
            clf = nfc.ContactlessFrontend(NFC_DEVICE_PATH)
            try:
                print("NFC listener started. Waiting for tags...")
                reconnect_delay = NFC_RECONNECT_INITIAL_DELAY

                while not stop_event.is_set():
                    target = clf.sense(
                        nfc.clf.RemoteTarget("106A"),
                        iterations=10,
                        interval=0.1,
                    )
                    if target is None:
                        continue

                    tag = nfc.tag.activate(clf, target)
                    if tag is None:
                        continue

                    handle_nfc_tag(tag, speaker, sharelink)
                    print("Tag processed.")
                    chipset = clf.device.chipset
                    chipset.set_buzzer_and_led_to_active(duration_in_ms=100)
                    chipset.send_ack()
                    chipset.set_buzzer_and_led_to_default()
                    stop_event.wait(12)
            finally:
                close_nfc_frontend_for_restart(clf)

        except (IOError, OSError) as error:
            if stop_event.is_set():
                break
            print(
                f"NFC reader error: {error}. "
                f"Reconnecting in {reconnect_delay} second(s)..."
            )
            stop_event.wait(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, NFC_RECONNECT_MAX_DELAY)
        except Exception as error:
            if stop_event.is_set():
                break
            print(
                f"Unexpected NFC error: {error}. "
                f"Reconnecting in {reconnect_delay} second(s)..."
            )
            stop_event.wait(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, NFC_RECONNECT_MAX_DELAY)
        finally:
            release_nfc_reader(reader_device, driver_detached)

def main():
    nfc_stop_event = threading.Event()
    nfc_thread = None

    try:
        while True:
            speaker = discovery.any_soco(allow_network_scan=True)
            if speaker is not None:
                break
            print("No speaker found via discovery. Trying direct connection to 192.168.0.103...")
            try:
                speaker = SoCo("192.168.0.103")
                if speaker is not None:
                    break
            except Exception as error:
                print(f"Failed to connect to speaker at 192.168.0.103: {error}")
            time.sleep(1)

        sharelink = ShareLinkPlugin(speaker)
        print(sharelink)
        print(speaker)

        # Start NFC listener in a separate thread. It is joined below so its USB
        # handle is released before this process exits.
        nfc_thread = threading.Thread(
            target=nfc_listener,
            args=(nfc_stop_event, speaker, sharelink),
            name="nfc-listener",
        )
        nfc_thread.start()

        print("Sonos REPL started. Commands: play, pause, next, -v [VOLUME], -a [URL], exit")

        while True:
            try:
                user_input = input(">> ").strip().split()
                if not user_input:
                    continue

                command = user_input[0]

                if command == "exit":
                    break

                elif command == "play":
                    speaker.play()
                    print("Playback resumed.")

                elif command == "pause":
                    speaker.pause()
                    print("Playback paused.")

                elif command == "next":
                    speaker.next()
                    print("Skipped to next track.")

                elif command == "queue":
                    queue = speaker.get_queue()
                    for item in queue:
                        print(item.title)

                elif command == "-v":
                    if len(user_input) < 2:
                        print("Error: Volume level missing (e.g., -v 30).")
                        continue
                    try:
                        volume = int(user_input[1])
                        speaker.volume = volume
                        print(f"Volume set to {volume}.")
                    except ValueError:
                        print("Error: Volume must be an integer (0-100).")

                elif command == "-a":
                    if len(user_input) < 2:
                        print("Error: URI missing (e.g., -a media:jellyfin/movie.mkv).")
                        continue

                    uri = user_input[1]
                    if uri.startswith(MEDIA_URI_PREFIX):
                        play_media_uri(uri)
                    elif "spotify" in uri:
                        kill_video()
                        play_uri(speaker, sharelink, uri)
                    else:
                        print(f"Unsupported URI: {uri}")

                else:
                    print("Error: Unknown command. Valid: play, pause, next, -v [VOLUME], -a [URL], exit")
            except Exception as error:
                print(f"Error: {error}")
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        nfc_stop_event.set()
        if nfc_thread is not None:
            nfc_thread.join()

if __name__ == "__main__":
    main()
