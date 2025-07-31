import sys
sys.path.insert(0, "./vendor")
from soco import SoCo, discovery
from soco.plugins.sharelink import ShareLinkPlugin

def main():
    speaker = discovery.any_soco()
    sharelink = ShareLinkPlugin(speaker)
    
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
                speaker.stop()  # Exit Spotify Connect
                # speaker.clear_queue()  # Reset Sonos queue
                sharelink.add_share_link_to_queue(url, position=0, as_next=True)
                speaker.play_from_queue(0)  # Force local queue playback
                print(f"Added {url} to the queue and started playback.")
            
            else:
                print("Error: Unknown command. Valid: play, pause, next, -v [VOLUME], -a [URL], exit")
        
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"Error: {str(e)}")

if __name__ == "__main__":
    main()
