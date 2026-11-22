"""Best-effort edge sound output, separate from durable audio ingestion.

The command-line clients use an installed player when available. A headless edge
can rely on the authenticated Simulator browser preview. Launching a player is not
proof that a speaker or sound device produced audible output.
"""
from pathlib import Path
import shutil
import subprocess


class SystemAudioPlayer:
    def __init__(self, *, enabled=True):
        self.process = None
        self.command = None
        if enabled:
            for name, options in (
                ("ffplay", ("-nodisp", "-autoexit", "-loglevel", "error", "-i")),
                ("paplay", ()), ("aplay", ("-q",)), ("afplay", ()),
            ):
                executable = shutil.which(name)
                if executable:
                    self.command = (executable, *options)
                    break

    @property
    def description(self):
        return Path(self.command[0]).name if self.command else "browser preview (no edge audio player)"

    def __call__(self, source: Path) -> bool:
        if self.command is None:
            return False
        # Validated paths are always a single argv element; no shell or command
        # string is constructed from scenario metadata or filenames.
        self.close()
        try:
            self.process = subprocess.Popen([*self.command, str(source.resolve())],
                                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            self.process = None
            return False
        return True

    def close(self):
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            except ProcessLookupError:
                pass
