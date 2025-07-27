import sys
import argparse
sys.path.insert(0, "./vendor")
from soco import SoCo 
from soco import discovery
from soco.plugins.sharelink import ShareLinkPlugin

parser = argparse.ArgumentParser(description='Control Sonos speaker.')
parser.add_argument('-pl', '--play', action='store_true', help='Play the current track')
parser.add_argument('-pa', '--pause', action='store_true', help='Pause the current track')
parser.add_argument('-n', '--next', action='store_true', help='Skip to the next track')
parser.add_argument('-v', '--volume', type=int, help='Set volume level (0-100)')
parser.add_argument('-a', '--add', help='Add a Spotify link to the queue')

args = parser.parse_args()

speaker = discovery.any_soco()
sharelink = ShareLinkPlugin(speaker)

queue = speaker.get_queue()
for item in queue:
    print(item.title)

if args.play:
    speaker.play()
elif args.pause:
    speaker.pause()
elif args.next:
    speaker.next()
elif args.volume is not None:
    speaker.volume = args.volume
elif args.add is not None:
    speaker.volume = 30
    speaker.stop()
    # speaker.clear_queue()
    # Add the track to the Sonos queue and play it next
    sharelink.add_share_link_to_queue(args.add, position=1, as_next=True)
    print(f"Added {args.add} to the Sonos queue.")
    # Start playback
    speaker.play_from_queue(0)
