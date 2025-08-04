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
        dev.detach_kernel_driver(1)
        print("Detached kernel driver.")
        try:
            dev.set_configuration()
            print(dev.configurations())
            print("Device claimed!")
        except Exception as e:
            print(f"Failed to claim: {e}")
    except Exception as e:
        print(f"Couldn't detach: {e}")

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
    try: 
        if hasattr(tag, 'ndef') and tag.ndef:
            for record in tag.ndef.records:
                if hasattr(record, 'uri') and record.uri:
                    speaker.volume = int(31)
                    play_uri(speaker, sharelink, record.uri)
                    print(f"\n>> -a {record.uri}")
                    return True
                    # return record.uri
        return True
    except Exception as e:
        print(f"error handling tag: {e}")
        return True


def nfc_senser(speaker, sharelink):
    reset_reader()
    max_retries = 3
    retry_delay = 3  # seconds

    while True:
        try:
            with nfc.ContactlessFrontend('usb:072f:2200') as clf:
                print("NFC listener started. Waiting for tags...")
                while True:
                    # Low-level sensing rather than relying solely on connect()
                    target = clf.sense(
                        nfc.clf.RemoteTarget('106A'),  # Type 2 tags are usually Type A (106A)
                        iterations=10,  # Short polling bursts
                        interval=0.1,
                        # options={'beep_on_connect':True}
                    )
                    if target is None:
                        continue  # No tag detected, quickly retry sensing
                    
                    # Tag detected: connect explicitly
                    tag = nfc.tag.activate(clf, target)
                    if tag:
                        handle_nfc_tag(tag, speaker, sharelink)
                        print("Tag processed and disconnected explicitly.")
                        chipset = clf.device.chipset
                        chipset.set_buzzer_and_led_to_active(duration_in_ms=100)
                        chipset.send_ack()
                        chipset.set_buzzer_and_led_to_default()
                    
                    time.sleep(0.5)  # Short delay for hardware stability

        except (IOError, OSError) as e:
            print(f"NFC reader error: {str(e)}")
            if max_retries <= 0:
                print("Max retries exceeded. NFC listener stopped.")
                break
            max_retries -= 1
            reset_reader()
            time.sleep(retry_delay)
        except Exception as e:
            print(f"Unexpected NFC error: {str(e)}")
            break

def nfc_listener(speaker, sharelink):
    """Listen for NFC tags and process them with retry logic."""
    reset_reader()
    max_retries = 3
    retry_delay = 3  # seconds
    
    while True:
        try:
            with nfc.ContactlessFrontend('usb') as clf:
                print("NFC listener started. Waiting for tags...")
                while True:
                    tag_processed = False
                    clf.connect(rdwr={
                        'on-connect': lambda tag: handle_nfc_tag(tag, speaker, sharelink),
                        'on-release': lambda tag: print(f"Tag {tag} released")
                    }, terminate=lambda: tag_processed)
                    tag_processed = True
                    print(f"tag processed? {tag_processed}")
                    time.sleep(0.5)
                    # reset_reader()

        except (IOError, OSError) as e:
            print(f"NFC reader error: {str(e)}")
            if max_retries <= 0:
                print("Max retries exceeded. NFC listener stopped.")
                break
            max_retries -= 1
            time.sleep(retry_delay)
            reset_reader()
            continue
        except Exception as e:
            print(f"Unexpected NFC error: {str(e)}")
            break

def main():
    while True:
        speaker = discovery.any_soco(allow_network_scan=True)
        if speaker is not None:
            break
        print("No speaker found via discovery. Trying direct connection to 192.168.0.103...")
        try:
            speaker = SoCo("192.168.0.103")
            if speaker is not None:
                break
        except Exception as e:
            print(f"Failed to connect to speaker at 192.168.0.103: {e}")
        time.sleep(1)
    
    sharelink = ShareLinkPlugin(speaker)
    print(sharelink)
    print(speaker)
    
    # Start NFC listener in a separate thread
    nfc_thread = threading.Thread(target=nfc_senser, args=(speaker, sharelink), daemon=True)
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
            
            else:
                print("Error: Unknown command. Valid: play, pause, next, -v [VOLUME], -a [URL], exit")
        
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"Error: {str(e)}")

if __name__ == "__main__":
    main()
