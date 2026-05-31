from soco.plugins.sharelink import ShareLinkPlugin
from soco import SoCo, discovery
import sys
import threading
import time
import nfc
import os
import subprocess
import ndef
from typing import Optional
from ndef.uri import UriRecord
from binascii import hexlify
import usb.core
sys.path.insert(0, "./vendor")

mount_point = "/tmp/jellyfin_mount"
mpv_process: Optional[subprocess.Popen] = None

def play_video(file_path):
    global mpv_process
    try:
        display_on()
        
        if mpv_process and mpv_process.poll() is None:
            mpv_process.terminate()
            mpv_process.wait()
        
        cmd = ["mpv", file_path, "--fullscreen", "--volume=80", "--really-quiet", "--keep-open=no"]
        mpv_process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, 
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"Playing: {file_path}")
        mpv_process.wait()
        display_off()
    except Exception as e:
        print(f"Failed to play: {e}")

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
    sharelink.add_share_link_to_queue(uri, position=1, as_next=True)
    speaker.play_from_queue(0)
    if 'playlist' in uri:
        # speaker.clear_queue()
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
                    if 'spotify' in uri:
                        play_uri(speaker, sharelink, uri)
                    elif 'jellyfin' in uri:
                        file_path = uri
                        play_video(f"{mount_point}/test@gmail.com/dockerbox/{file_path}")
                    print(f"\n>> -a {uri}")
                    return True
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
                        time.sleep(12)
                    
                    time.sleep(0.5)  # Short delay for hardware stability

        except (IOError, OSError) as e:
            print(f"NFC reader error: {str(e)}")
            if max_retries <= 0:
                print("Max retries exceeded. NFC listener stopped.")
                break
            max_retries -= 1
            reset_reader()
            time.sleep(retry_delay)

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
                print(user_input)
                if 'spotify' in user_input: 
                    if len(user_input) < 2:
                        print("Error: URL missing (e.g., -a https://spotify...).")
                        continue
                    url = user_input[1]
                    play_uri(speaker, sharelink, url)
                if 'jellyfin' in user_input:
                    if len(user_input) < 2:
                        file_path = f"jellyfin/Zelda/Twilight Princess Full Soundtrack.mkv"
                    else:
                        file_path = user_input[1]
                    print(f"Playing: {file_path}")
                    play_video(f"{mount_point}/test@gmail.com/dockerbox/{file_path}")
            
            else:
                print("Error: Unknown command. Valid: play, pause, next, -v [VOLUME], -a [URL], exit")
        
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"Error: {str(e)}")

if __name__ == "__main__":
    main()
