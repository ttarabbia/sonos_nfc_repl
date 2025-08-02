import sys
import threading
import time
import nfc
import ndef
from ndef.uri import UriRecord
from binascii import hexlify
import usb.core
sys.path.insert(0, "./vendor")
from soco import SoCo, discovery
from soco.plugins.sharelink import ShareLinkPlugin

def reset_reader():
    """Reset the NFC reader USB connection."""
    dev = usb.core.find(idVendor=0x072f, idProduct=0x2200)
    if dev is None:
        raise ValueError("Device not found")
    
    try:
        dev.detach_kernel_driver(0)
        print("Detached kernel driver.")
    except Exception as e:
        print(f"Couldn't detach: {e}")
    
    try:
        dev.set_configuration()
        print("Device claimed!")
    except Exception as e:
        print(f"Failed to claim: {e}")

def play_uri(speaker, sharelink, uri):
    """Play a URI by adding it to the queue and starting playback."""
    speaker.stop()
    speaker.clear_queue()
    sharelink.add_share_link_to_queue(uri, position=1, as_next=True)
    speaker.play_from_queue(0)
    print(f"Added {uri} to the queue and started playback.")
    return uri

def handle_nfc_tag(tag, speaker, sharelink):
    """Handle NFC tag detection and read the URI."""
    if hasattr(tag, 'ndef') and tag.ndef:
        for record in tag.ndef.records:
            if hasattr(record, 'uri'):
                if record.uri:
                    play_uri(speaker, sharelink, record.uri)
                    print(f"\n>> -a {record.uri}")
                    reset_reader()
                return record.uri
    return None

def nfc_listener(speaker, sharelink):
    """Listen for NFC tags and process them with retry logic."""
    max_retries = 3
    retry_delay = 2  # seconds
    
    while True:
        try:
            reset_reader()  # Reset USB connection before each attempt
            with nfc.ContactlessFrontend('usb') as clf:
                print("NFC listener started. Waiting for tags...")
                while True:
                    clf.connect(rdwr={
                        'on-connect': lambda tag: handle_nfc_tag(tag, speaker, sharelink) or True,
                        'on-release': lambda tag: None
                    })

        except (IOError, OSError) as e:
            print(f"NFC reader error: {str(e)}")
            if max_retries <= 0:
                print("Max retries exceeded. NFC listener stopped.")
                break
            max_retries -= 1
            time.sleep(retry_delay)
            continue
        except Exception as e:
            print(f"Unexpected NFC error: {str(e)}")
            break

def main():
    speaker = discovery.any_soco()
    sharelink = ShareLinkPlugin(speaker)
    print(sharelink)
    
    # Start NFC listener in a separate thread
    nfc_thread = threading.Thread(target=nfc_listener, args=(speaker, sharelink), daemon=True)
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
                    print("Error: URL missing (e.g., -a https://spotify...).")
                    continue
                url = user_input[1]
                play_uri(speaker, sharelink, url)
                reset_reader()
            
            else:
                print("Error: Unknown command. Valid: play, pause, next, -v [VOLUME], -a [URL], exit")
        
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"Error: {str(e)}")

if __name__ == "__main__":
    main()
