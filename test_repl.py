import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repl import Command, CommandStack, available_videos, create_app, handle_nfc_tag, resolve_video_path
from starlette.testclient import TestClient


class FakeSpeaker:
    def __init__(self):
        self.calls = []
        self.volume = 20

    def play(self):
        self.calls.append("play")

    def pause(self):
        self.calls.append("pause")

    def next(self):
        self.calls.append("next")


class CommandStackTests(unittest.TestCase):
    def setUp(self):
        self.stack = CommandStack(start_worker=False)
        self.speaker = FakeSpeaker()
        self.stack.speaker = self.speaker

    def test_playback_and_volume_commands_execute_serially(self):
        self.stack.execute(Command("play"))
        self.stack.execute(Command("pause"))
        self.stack.execute(Command("volume", "42"))

        self.assertEqual(self.speaker.calls, ["play", "pause"])
        self.assertEqual(self.speaker.volume, 42)
        self.assertEqual(self.stack.snapshot()["volume"], 42)

    def test_volume_must_be_in_range(self):
        with self.assertRaises(ValueError):
            self.stack.execute(Command("volume", "101"))


class MediaPathTests(unittest.TestCase):
    def test_only_videos_inside_media_root_are_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "Films" / "example.mp4"
            video.parent.mkdir()
            video.touch()
            (root / "notes.txt").touch()

            self.assertEqual(resolve_video_path("Films/example.mp4", root), video.resolve())
            self.assertIsNone(resolve_video_path("../outside.mp4", root))
            self.assertEqual(available_videos(root), [video.resolve()])

    def test_unavailable_directory_does_not_hide_other_videos(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "available.mp4"
            video.touch()

            def walk_with_error(path, onerror):
                onerror(FileNotFoundError(2, "No such file or directory", str(root / "missing")))
                yield str(root), [], [video.name]

            with patch("repl.os.walk", walk_with_error):
                self.assertEqual(available_videos(root), [video.resolve()])


class WebAppTests(unittest.TestCase):
    def test_controls_enqueue_posted_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "clip.mp4").touch()
            stack = CommandStack(root, start_worker=False)
            client = TestClient(create_app(stack))

            home = client.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertIn("clip.mp4", home.text)
            response = client.post(
                "/volume",
                data={"volume": "37"},
                headers={"Accept": "application/json"},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 204)
            self.assertEqual(stack.commands.get_nowait(), Command("volume", "37", "web"))

            response = client.post(
                "/video",
                data={"video": "clip.mp4"},
                headers={"Accept": "application/json"},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 204)
            self.assertEqual(stack.commands.get_nowait(), Command("video", "clip.mp4", "web"))

            response = client.post("/command/stop-video", follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(stack.commands.get_nowait(), Command("stop_video", None, "web"))


class NfcTests(unittest.TestCase):
    def test_tag_commands_share_the_same_queue(self):
        record = type("Record", (), {"uri": "https://open.spotify.com/track/example"})()
        tag = type("Tag", (), {"ndef": type("Ndef", (), {"records": [record]})()})()
        stack = CommandStack(start_worker=False)

        self.assertTrue(handle_nfc_tag(tag, stack))
        self.assertEqual(stack.commands.get_nowait(), Command("volume", "23", "nfc"))
        self.assertEqual(stack.commands.get_nowait(), Command("uri", record.uri, "nfc"))


if __name__ == "__main__":
    unittest.main()
