"""Sets up poke watch on this Windows PC for the shops that block GitHub's servers
(Public, Kaissa and Nerdom). GitHub keeps checking every other shop.

Run it by double-clicking setup_pc.bat, which creates the Python environment first.
Safe to run again: it keeps your topic and replaces the scheduled task.
  setup_pc.bat            install, test, and check those shops every hour from this PC
  remove_pc_task.bat      stop the hourly check
"""
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "poke_watch.py"
ENV_FILE = HERE / "poke_watch.env"
SHOPS = "Public,Kaissa,Nerdom"   # the shops that block GitHub's servers
TASK = "Poke watch"
MINUTE = 47                      # runs at :47 every hour (GitHub runs at :17)
PY = Path(sys.executable)        # this environment's python.exe
PYW = PY.with_name("pythonw.exe")  # same, without a console window
TOPIC_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def say(text: str = "") -> None:
    print(text, flush=True)


def step(text: str) -> None:
    say()
    say("== " + text)


def ask(question: str) -> str:
    return input(question).strip()


def yes(question: str, default: bool) -> bool:
    answer = ask(question + (" (Y/n) " if default else " (y/N) ")).lower()
    return default if not answer else answer.startswith("y")


def run(args: list, what: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run([str(a) for a in args])
    if check and result.returncode != 0:
        raise SystemExit(f"\n{what} failed (exit code {result.returncode}). See the messages above.")
    return result


# ------------------------------------------------------------------ topic

def read_topic() -> str:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("NTFY_TOPIC="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def write_topic(topic: str) -> None:
    keep = []
    if ENV_FILE.exists():
        keep = [line for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines()
                if not line.strip().startswith("NTFY_TOPIC=")]
    ENV_FILE.write_text("\n".join([f"NTFY_TOPIC={topic}"] + keep) + "\n", encoding="utf-8")


def ask_topic() -> str:
    while True:
        topic = ask("Your ntfy topic name, exactly as in the ntfy app: ")
        for prefix in ("https://", "http://", "ntfy.sh/"):
            topic = topic[len(prefix):] if topic.lower().startswith(prefix) else topic
        if TOPIC_RE.fullmatch(topic):
            return topic
        say("A topic name has only letters, numbers, - and _ (no spaces). Try again.")


def test_push() -> None:
    step("Sending a test push to your phone")
    if not read_topic():
        write_topic(ask_topic())
    for _ in range(4):
        sent = run([PY, SCRIPT, "--notify-test"], "Test push", check=False).returncode == 0
        if sent and yes("Did 'Poke watch test' arrive on your phone?", default=True):
            return
        if sent:
            say("Then the topic here doesn't match the one in the ntfy app (it's case sensitive).")
            write_topic(ask_topic())
        else:
            retry = ask("Type the topic again if it was wrong, or just press Enter to try again: ")
            if retry:
                if TOPIC_RE.fullmatch(retry):
                    write_topic(retry)
                else:
                    say("That isn't a valid topic name, keeping the old one.")
    raise SystemExit("\nCouldn't get a test push through. Check the topic and your internet connection, "
                     "then run setup_pc.bat again.")


# ------------------------------------------------------------------ checks

def test_shops() -> list:
    """One dry run of the three shops from this PC. Returns the shops that failed, with the reason."""
    step(f"Checking {SHOPS.replace(',', ', ')} once from this PC (nothing is sent)")
    proc = subprocess.Popen([str(PY), str(SCRIPT), "--test", "--shop", SHOPS], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    failed = []
    for line in proc.stdout:
        print(line, end="", flush=True)
        m = re.search(r"\[(.+?)\] check FAILED: (.+)", line)
        if m:
            failed.append((m.group(1), m.group(2).strip()))
    proc.wait()
    if proc.returncode != 0 and not failed:
        raise SystemExit(f"\nThe test check crashed (exit code {proc.returncode}). See the messages above.")
    return failed


def schedule() -> None:
    step(f"Scheduling the check every hour at :{MINUTE} (Windows Task Scheduler)")
    now = dt.datetime.now()
    start = now.replace(minute=MINUTE, second=0, microsecond=0)
    if start <= now + dt.timedelta(minutes=1):
        start += dt.timedelta(hours=1)
    command = f'"{PYW}" "{SCRIPT}" --shop {SHOPS}'
    if len(command) > 261:
        raise SystemExit("\nThis folder's path is too long for Task Scheduler. Move the folder somewhere "
                         "shorter, like C:\\poke-watch, and run setup_pc.bat again.")
    run(["schtasks", "/Create", "/F", "/TN", TASK, "/SC", "HOURLY", "/MO", "1",
         "/ST", start.strftime("%H:%M"), "/TR", command], "Creating the scheduled task")
    # Task Scheduler's defaults skip runs on battery power and pile up runs; fix both.
    settings = ("$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
                "-StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30); "
                f"Set-ScheduledTask -TaskName '{TASK}' -Settings $s | Out-Null")
    tweaked = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", settings],
                             capture_output=True, text=True)
    if tweaked.returncode != 0:
        say("(Couldn't change the task's power settings, so on a laptop it may only run while plugged in.)")
    run(["schtasks", "/Run", "/TN", TASK], "Starting the first check", check=False)


# ------------------------------------------------------------------ main

def main() -> int:
    say("Poke watch: setting up this PC to check Public, Kaissa and Nerdom every hour.")
    say(f"Folder: {HERE}")
    where = str(HERE).lower()
    if "onedrive" in where or "\\downloads\\" in where + "\\":
        say("\nThis folder is in OneDrive or Downloads. The hourly check runs from here, so it's better in a")
        say("folder you won't clean up or sync, like C:\\Users\\<you>\\poke-watch.")
        if not yes("Continue here anyway?", default=False):
            say("Move the folder, then double-click setup_pc.bat again.")
            return 1

    step("Installing what the checker needs (the first time takes a few minutes)")
    run([PY, "-m", "pip", "install", "--disable-pip-version-check", "-r", HERE / "requirements.txt"],
        "Installing Playwright")
    run([PY, "-m", "playwright", "install", "chromium"], "Downloading the Chromium browser")

    test_push()

    failed = test_shops()
    if failed:
        say()
        for shop, reason in failed:
            say(f"  {shop} didn't let the check in from this PC either: {reason}")
        if len(failed) == len(SHOPS.split(",")):
            if not yes("None of the three worked. Schedule the hourly check anyway?", default=False):
                say("Nothing scheduled. You can run setup_pc.bat again later.")
                return 1
        else:
            say("  The others worked. If a shop keeps failing, you get one warning push about it.")
    else:
        say("\nAll three shops answered from this PC.")

    schedule()
    say()
    say("Done. This PC now checks Public, Kaissa and Nerdom every hour while it's on and awake;")
    say("GitHub keeps checking every other shop around the clock.")
    say("The first real check is running in the background now. Anything at retail price goes")
    say("to your phone. What each run did is written to:")
    say(f"  {HERE / 'poke_watch.log'}")
    say("Don't move or delete this folder: the hourly check runs from it.")
    say("To stop it, double-click remove_pc_task.bat.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\nStopped.")
        sys.exit(1)
