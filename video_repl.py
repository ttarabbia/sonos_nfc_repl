import sys
import subprocess
import threading
from queue import Queue


class MPVRepl:
    def __init__(self):
        self.process = None
        self.command_queue = Queue()

    def play(self, file_path):
        if self.process and self.process.poll() is None:
            self.stop()

        cmd = [
            "mpv",
            file_path,
            "--fullscreen",
           "--volume=80",
           "--really-quiet",
        ]
        print(cmd)

        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait()
            self.process = None

    def start_repl(self):
        print("MPV REPL started. Commands: play <file_path>, stop, quit")

        while True:
            try:
                user_input = input("> ").strip()
                if not user_input:
                    continue

                parts = user_input.split(maxsplit=1)
                command = parts[0].lower()

                if command == "quit":
                    self.stop()
                    break
                elif command == "stop":
                    self.stop()
                    print("Playback stopped")
                elif command == "play":
                    if len(parts) < 2:
                        # file_path = "/mnt/jellyfin/test@gmail.com/dockerbox/jellyfin/Asterix et Obelix Chez Les Bretons/ASTERIX ET OBELIX CHEZ LES BRETONS FR HD [FTgjgyuivfk].webm"
                        file_path = "/Volumes/100.100.100.100/test@gmail.com/dockerbox/jellyfin/Asterix et Obelix Chez Les Bretons/ASTERIX ET OBELIX CHEZ LES BRETONS FR HD [FTgjgyuivfk].webm"
                    else:
                        file_path = parts[1]
                    print(f"Playing: {file_path}")
                    self.play(file_path)
                else:
                    print("Unknown command. Available: play, stop, quit")

            except KeyboardInterrupt:
                self.stop()
                break
            except EOFError:
                self.stop()
                break


if __name__ == "__main__":
    repl = MPVRepl()
    repl.start_repl()
