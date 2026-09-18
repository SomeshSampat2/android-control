from argparse import ArgumentParser
from contextlib import asynccontextmanager
from dataclasses import dataclass
from textwrap import dedent
from typing import Literal, Optional
import asyncio
import os
import subprocess
import time

from fastmcp import FastMCP
from fastmcp.utilities.types import Image
from mcp.types import ToolAnnotations

from android_mcp.mobile.service import Mobile
from android_mcp.jev.service import Jev, JevNotConfigured

parser = ArgumentParser()
parser.add_argument("--device", type=str, help="ADB device serial or host:port")
parser.add_argument(
    "--connection",
    "--transport",
    dest="connection",
    choices=("auto", "usb", "wifi"),
    help="Preferred device connection type",
)
parser.add_argument(
    "--wifi",
    nargs="?",
    const="",
    metavar="HOST",
    help="Use WiFi ADB. Accepts HOST or HOST:PORT and defaults to port 5555.",
)
parser.add_argument(
    "--usb",
    nargs="?",
    const="",
    metavar="SERIAL",
    help="Use USB ADB. Optionally provide a specific device serial.",
)
args, _ = parser.parse_known_args()

instructions = dedent(
    """
    Android MCP server provides tools to interact directly with the Android device,
    thus enabling to operate the mobile device like an actual USER.
    """
)


@dataclass(frozen=True)
class DevicePreference:
    connection: str = "auto"
    serial: Optional[str] = None
    source: str = "auto-detect"


def _clean_env(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _normalize_connection(value: Optional[str]) -> str:
    if not value:
        return "auto"
    normalized = value.strip().lower()
    if normalized in {"usb", "wifi", "auto"}:
        return normalized
    raise RuntimeError(
        "Invalid connection type. Use --connection auto|usb|wifi or ANDROID_MCP_CONNECTION."
    )


def _configured_preference() -> DevicePreference:
    env_device = _clean_env("ANDROID_MCP_DEVICE")
    env_connection = _normalize_connection(_clean_env("ANDROID_MCP_CONNECTION"))
    env_host = _clean_env("ANDROID_MCP_HOST")

    if args.wifi is not None:
        serial = Mobile.normalize_wifi_serial(args.wifi or env_host)
        return DevicePreference(connection="wifi", serial=serial, source="--wifi")

    if args.usb is not None:
        serial = args.usb.strip() if args.usb else None
        return DevicePreference(connection="usb", serial=serial or None, source="--usb")

    if args.device:
        return DevicePreference(
            connection=_normalize_connection(args.connection) if args.connection else "auto",
            serial=args.device.strip(),
            source="--device",
        )

    if env_device:
        return DevicePreference(connection=env_connection, serial=env_device, source="ANDROID_MCP_DEVICE")

    if env_connection == "wifi" or env_host:
        serial = Mobile.normalize_wifi_serial(env_host)
        return DevicePreference(connection="wifi", serial=serial, source="ANDROID_MCP_CONNECTION/ANDROID_MCP_HOST")

    return DevicePreference(
        connection=_normalize_connection(args.connection) if args.connection else env_connection,
        serial=None,
        source="auto-detect",
    )


def _format_available_devices() -> str:
    devices = Mobile.list_devices()
    online = [(serial, state) for serial, state in devices if state == "device"]
    if not online:
        return ""
    formatted = ", ".join(serial for serial, _ in online)
    return f" Available devices: {formatted}."


def _pick_auto_device(connection: str) -> Optional[str]:
    devices = Mobile.list_devices()
    online = [serial for serial, state in devices if state == "device"]
    if not online:
        return None

    if connection == "wifi":
        for serial in online:
            if ":" in serial:
                return serial
        return None

    if connection == "usb":
        for serial in online:
            if ":" not in serial:
                return serial
        return None

    return online[0]


def _resolve_target() -> DevicePreference:
    preference = _configured_preference()

    if preference.serial:
        serial = preference.serial
        if preference.connection == "wifi":
            serial = Mobile.normalize_wifi_serial(serial)
        return DevicePreference(
            connection=preference.connection,
            serial=serial,
            source=preference.source,
        )

    serial = _pick_auto_device(preference.connection)
    if serial:
        return DevicePreference(
            connection=preference.connection,
            serial=serial,
            source="auto-detect",
        )

    return preference


def _not_configured_message() -> str:
    return (
        "No device configured. Use --device flag, --wifi, --usb, or ANDROID_MCP_DEVICE."
        + _format_available_devices()
    )


def _connect_preferred_device() -> None:
    if mobile.is_connected:
        return

    target = _resolve_target()
    if not target.serial:
        raise RuntimeError(_not_configured_message())

    serial = target.serial
    if target.connection == "wifi" or ":" in serial:
        serial = Mobile.normalize_wifi_serial(serial)
        Mobile.adb_connect(serial)

    mobile.connect(serial)


@asynccontextmanager
async def lifespan(app: FastMCP):
    """Runs initialization code before the server starts and cleanup code after it shuts down."""
    await asyncio.sleep(1)
    yield


mcp = FastMCP(name="Android-Control", instructions=instructions)
mobile = Mobile()
jev = Jev(mobile)


def require_device():
    _connect_preferred_device()
    return mobile.get_device()

@mcp.tool(
    name="Device",
    description="Manage ADB devices (list, connect, or disconnect)",
    annotations=ToolAnnotations(title="Device"),
)
def device_tool(action: Literal["list", "connect", "disconnect"], serial: Optional[str] = None):
    if action == "list":
        devices = Mobile.list_devices()
        if not devices:
            return "No devices found. Ensure a device is connected and ADB is running."
        lines = [f"{device_serial}\t{state}" for device_serial, state in devices]
        return "\n".join(lines)
    if action == "connect":
        target = serial
        if not target:
            resolved = _resolve_target()
            target = resolved.serial
            if not target:
                return _not_configured_message()
            if resolved.connection == "wifi":
                target = Mobile.normalize_wifi_serial(target)
                Mobile.adb_connect(target)
        elif ":" in target:
            target = Mobile.normalize_wifi_serial(target)
            Mobile.adb_connect(target)
        mobile.connect(target)
        return f"Connected to {target}"
    if action == "disconnect":
        mobile.disconnect()
        return "Disconnected from device."
    return f"Unknown action: {action}"


@mcp.tool(
    name="Click",
    description="Click on a specific cordinate",
    annotations=ToolAnnotations(title="Click", destructiveHint=True),
)
def click_tool(x: int, y: int):
    device = require_device()
    device.click(x, y)
    return f"Clicked on ({x},{y})"


@mcp.tool(
    name="Snapshot",
    description="Get the state of the device. Optionally includes visual screenshot when use_vision=True. The use_annotation parameter (default True) can be set to False to get a clean screenshot without bounding boxes.",
    annotations=ToolAnnotations(title="Snapshot", readOnlyHint=True),
)
def state_tool(use_vision: bool = False, use_annotation: bool = True):
    require_device()
    mobile_state = mobile.get_state(
        use_vision=use_vision, use_annotation=use_annotation, as_bytes=True
    )
    return [mobile_state.tree_state.to_string()] + (
        [Image(data=mobile_state.screenshot, format="PNG")] if use_vision else []
    )


@mcp.tool(
    name="LongClick",
    description="Long click on a specific cordinate",
    annotations=ToolAnnotations(title="Long Click", destructiveHint=True),
)
def long_click_tool(x: int, y: int):
    device = require_device()
    device.long_click(x, y)
    return f"Long Clicked on ({x},{y})"


@mcp.tool(
    name="Swipe",
    description="Swipe on a specific cordinate",
    annotations=ToolAnnotations(title="Swipe", destructiveHint=True),
)
def swipe_tool(x1: int, y1: int, x2: int, y2: int):
    device = require_device()
    device.swipe(x1, y1, x2, y2)
    return f"Swiped from ({x1},{y1}) to ({x2},{y2})"



@mcp.tool(
    name="Drag",
    description="Drag from location and drop on another location",
    annotations=ToolAnnotations(title="Drag", destructiveHint=True),
)
def drag_tool(x1: int, y1: int, x2: int, y2: int):
    device = require_device()
    device.drag(x1, y1, x2, y2)
    return f"Dragged from ({x1},{y1}) and dropped on ({x2},{y2})"



@mcp.tool(
    name="Notification",
    description="Access the notifications seen on the device",
    annotations=ToolAnnotations(
        title="Notification", destructiveHint=True, idempotentHint=True
    ),
)
def notification_tool():
    device = require_device()
    device.open_notification()
    return "Accessed notification bar"


@mcp.tool(
    name="Wait",
    description="Wait for a specific amount of time",
    annotations=ToolAnnotations(title="Wait", destructiveHint=True, idempotentHint=True),
)
def wait_tool(duration: int):
    device = require_device()
    device.sleep(duration)
    return f"Waited for {duration} seconds"



# App Management Tools
@mcp.tool(name='LaunchApp', description='Launch a specific app by package name', annotations=ToolAnnotations(title="Launch App", destructiveHint=True))
def launch_app_tool(package_name: str, activity: str = None):
    try:
        device = require_device()
        if activity:
            device.app_start(package_name, activity)
        else:
            device.app_start(package_name)
        return f"Launched {package_name}"
    except Exception as e:
        return f"Error launching app: {str(e)}"

@mcp.tool(name='StopApp', description='Stop/force-stop a specific app', annotations=ToolAnnotations(title="Stop App", destructiveHint=True))
def stop_app_tool(package_name: str):
    try:
        device = require_device()
        device.app_stop(package_name)
        return f"Stopped {package_name}"
    except Exception as e:
        return f"Error stopping app: {str(e)}"

@mcp.tool(name='GetCurrentApp', description='Get the currently running app package and activity', annotations=ToolAnnotations(title="Get Current App", readOnlyHint=True))
def get_current_app_tool():
    try:
        device = require_device()
        info = device.app_current()
        return f"Package: {info.get('package')}, Activity: {info.get('activity')}"
    except Exception as e:
        return f"Error getting current app: {str(e)}"

@mcp.tool(name='ListApps', description='List all installed apps', annotations=ToolAnnotations(title="List Apps", readOnlyHint=True))
def list_apps_tool():
    try:
        device = require_device()
        apps = device.app_list()
        return "\n".join(apps[:50])  # Limit to first 50 apps to avoid overwhelming output
    except Exception as e:
        return f"Error listing apps: {str(e)}"



@mcp.tool(name='PullToRefresh', description='Perform a pull-to-refresh gesture by scrolling to top and pulling down significantly to trigger refresh', annotations=ToolAnnotations(title="Pull To Refresh", destructiveHint=True))
def pull_to_refresh_tool(pull_distance: int = 1200):
    try:
        device = require_device()
        # First, scroll to the top of the page (multiple up scrolls to ensure we're at top)
        for _ in range(3):
            device.swipe(540, 500, 540, 1200)
            device.sleep(0.2)
        
        # Perform the pull-to-refresh gesture from the top of the screen
        # Start from near the top and pull down significantly to trigger refresh
        device.swipe(540, 200, 540, 200 + pull_distance, duration=0.5)
        device.sleep(0.5)
        
        return f"Pull-to-refresh gesture performed with {pull_distance}px pull distance"
    except Exception as e:
        return f"Error performing pull-to-refresh: {str(e)}"


# Clipboard Tools
@mcp.tool(name='GetClipboard', description='Get text from clipboard', annotations=ToolAnnotations(title="Get Clipboard", readOnlyHint=True))
def get_clipboard_tool():
    try:
        device = require_device()
        # Use uiautomator2 to get clipboard
        clipboard = device.clipboard
        return f"Clipboard content: {clipboard}"
    except Exception as e:
        return f"Error getting clipboard: {str(e)}"

@mcp.tool(name='SetClipboard', description='Set text to clipboard', annotations=ToolAnnotations(title="Set Clipboard", destructiveHint=True))
def set_clipboard_tool(text: str):
    try:
        device = require_device()
        device.clipboard = text
        return f"Set clipboard to: {text}"
    except Exception as e:
        return f"Error setting clipboard: {str(e)}"


# Testing/Debugging Tools
@mcp.tool(name='ExecuteShell', description='Execute a shell command on device', annotations=ToolAnnotations(title="Execute Shell", destructiveHint=True))
def execute_shell_tool(command: str):
    try:
        result = subprocess.run(['adb', 'shell', command], capture_output=True, text=True, timeout=30)
        output = result.stdout if result.stdout else result.stderr
        return f"Command: {command}\nOutput: {output}"
    except subprocess.TimeoutExpired:
        return f"Command timed out: {command}"
    except Exception as e:
        return f"Error executing shell command: {str(e)}"

@mcp.tool(name='GetLogs', description='Get Android logcat logs', annotations=ToolAnnotations(title="Get Logs", readOnlyHint=True))
def get_logs_tool(tag: str = None, lines: int = 100):
    try:
        cmd = ['adb', 'logcat', '-d', '-t', str(lines)]
        if tag:
            cmd.extend(['-s', tag])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout if result.stdout else "No logs found"
    except subprocess.TimeoutExpired:
        return "Logcat command timed out"
    except Exception as e:
        return f"Error getting logs: {str(e)}"

@mcp.tool(name='ClearLogs', description='Clear logcat buffer', annotations=ToolAnnotations(title="Clear Logs", destructiveHint=True))
def clear_logs_tool():
    try:
        subprocess.run(['adb', 'logcat', '-c'], timeout=10)
        return "Logs cleared"
    except Exception as e:
        return f"Error clearing logs: {str(e)}"

@mcp.tool(name='GetDeviceInfo', description='Get detailed device information', annotations=ToolAnnotations(title="Get Device Info", readOnlyHint=True))
def get_device_info_tool():
    try:
        device = require_device()
        info = device.info
        output = f"Model: {info.get('model')}\n"
        output += f"Android Version: {info.get('version')}\n"
        output += f"SDK: {info.get('sdk')}\n"
        output += f"Resolution: {info.get('width')}x{info.get('height')}\n"
        output += f"Brand: {info.get('brand')}\n"
        return output
    except Exception as e:
        return f"Error getting device info: {str(e)}"


# System Control Tools
@mcp.tool(name='SetOrientation', description='Set screen orientation (portrait, landscape, auto)', annotations=ToolAnnotations(title="Set Orientation", destructiveHint=True))
def set_orientation_tool(orientation: str):
    try:
        device = require_device()
        if orientation == "portrait":
            device.orientation = "natural"
        elif orientation == "landscape":
            device.orientation = "left"
        elif orientation == "auto":
            device.orientation = "unlocked"
        else:
            return "Error: orientation must be 'portrait', 'landscape', or 'auto'"
        return f"Orientation set to {orientation}"
    except Exception as e:
        return f"Error setting orientation: {str(e)}"

@mcp.tool(name='PressKey', description='Press a physical key (home, back, volume_up, volume_down, etc.)', annotations=ToolAnnotations(title="Press Key", destructiveHint=True))
def press_key_tool(key: str):
    try:
        device = require_device()
        device.press(key)
        return f"Pressed {key}"
    except Exception as e:
        return f"Error pressing key: {str(e)}"


@mcp.tool(name='WaitForActivity', description='Wait for a specific activity to be in foreground', annotations=ToolAnnotations(title="Wait For Activity", readOnlyHint=True))
def wait_for_activity_tool(activity_name: str, timeout: float = 10.0):
    try:
        device = require_device()
        start = time.time()
        while time.time() - start < timeout:
            current = device.app_current()
            if activity_name in current.get('activity', ''):
                return f"Activity '{activity_name}' is now in foreground"
            time.sleep(0.5)
        return f"Activity '{activity_name}' not found within {timeout}s"
    except Exception as e:
        return f"Error waiting for activity: {str(e)}"


# Jev (TypeSafe System One) fast-decision tools
# Jev makes the per-step judgments (which element, which action, done yet) in one
# ~0.3s parallel call; code owns the loop and execution; you (the LLM) supply
# goals and any text through the tool arguments.
def _jev_unavailable() -> Optional[str]:
    if not jev.sdk_available:
        return "typesafe-sdk is not installed. Add it with `uv add typesafe-sdk`."
    if not jev.is_configured:
        return (
            "TYPESAFE_API_KEY is not set. Get a key at "
            "https://console.typesafe.ai/settings/keys and add it to the MCP "
            "server environment. Until then use Snapshot + the standard tools."
        )
    return None


def _jev_execute(device, decision: dict, text_to_type: Optional[str] = None) -> str:
    action = decision.get("action")
    el = decision.get("element")
    if action == "tap_element":
        if el is None:
            return "no tap target chosen"
        device.click(el.coordinates.x, el.coordinates.y)
        return (f"tapped element {decision['element_index']} '{el.name}' "
                f"at ({el.coordinates.x},{el.coordinates.y})")
    if action == "type_text":
        if el is None:
            return "no type target chosen"
        if text_to_type is None:
            return "type_text chosen but no text_to_type was provided"
        device.click(el.coordinates.x, el.coordinates.y)
        device.sleep(0.3)
        device.set_fastinput_ime(enable=True)
        device.send_keys(text=text_to_type, clear=True)
        jev.mark_typed(el)
        return (f"typed \"{text_to_type}\" into element "
                f"{decision['element_index']} '{el.name}'")
    if action == "scroll_down":
        device.swipe(540, 1500, 540, 800)
        return "scrolled down"
    if action == "scroll_up":
        device.swipe(540, 800, 540, 1500)
        return "scrolled up"
    if action == "go_back":
        device.press("back")
        return "pressed back"
    if action == "go_home":
        device.press("home")
        return "pressed home"
    if action == "press_enter":
        device.press("enter")
        return "pressed enter"
    if action == "wait":
        device.sleep(1.5)
        return "waited 1.5s"
    return f"no execution needed for action '{action}'"


def _format_alts(alts: list) -> str:
    return ", ".join(f"{a['option']} ({a['probability']})" for a in alts)


@mcp.tool(name='JevStatus', description='Check whether the Jev fast-decision layer is ready: typesafe-sdk installed, TYPESAFE_API_KEY set, model and element cap.', annotations=ToolAnnotations(title="Jev Status", readOnlyHint=True))
def jev_status_tool():
    s = jev.status
    lines = [
        f"configured: {s['configured']}",
        f"sdk_installed: {s['sdk_installed']}",
        f"model: {s['model']}",
        f"max_elements: {s['max_elements']}",
    ]
    if not s["sdk_installed"]:
        lines.append("Install with: uv add typesafe-sdk")
    if not s["configured"]:
        lines.append("Set TYPESAFE_API_KEY in the MCP server environment.")
    return "\n".join(lines)


@mcp.tool(name='JevTap', description='Tap the on-screen element matching a natural-language description (e.g. "the search icon", "Sign in button"). Jev decides which element in one fast call — much faster than reading a Snapshot and clicking by hand. When unsure it returns candidates instead of tapping blindly.', annotations=ToolAnnotations(title="Jev Tap", destructiveHint=True))
def jev_tap_tool(target: str):
    guard = _jev_unavailable()
    if guard:
        return guard
    device = require_device()
    try:
        res = jev.pick_element(target)
    except Exception as e:
        return f"Jev decision failed: {e}"
    if not res["found"] or res["confidence"] < 0.3:
        alts = _format_alts(res["alternatives"])
        return (
            f"Jev found no confident match for '{target}' "
            f"(presence={res['presence']}, confidence={res['confidence']}, "
            f"elements={res['element_count']}). Top candidates: {alts}. "
            "Rephrase the target, take a Snapshot, or use Click with coordinates."
        )
    el = res["element"]
    device.click(el.coordinates.x, el.coordinates.y)
    return (
        f"Jev tapped element {res['element_index']} '{el.name}' "
        f"at ({el.coordinates.x},{el.coordinates.y}) "
        f"(presence={res['presence']}, confidence={res['confidence']})"
    )


@mcp.tool(name='JevType', description='Type text into the best-matching input field on screen. You supply the text; Jev picks the field, focuses it, and code types. Optionally describe the field (e.g. "search box") to disambiguate when several inputs exist.', annotations=ToolAnnotations(title="Jev Type", destructiveHint=True))
def jev_type_tool(text: str, field: Optional[str] = None):
    guard = _jev_unavailable()
    if guard:
        return guard
    device = require_device()
    target = field or f"the text input field that should receive: {text!r}"
    try:
        res = jev.pick_element(target)
    except Exception as e:
        return f"Jev decision failed: {e}"
    if not res["found"]:
        alts = _format_alts(res["alternatives"])
        return (
            f"Jev found no confident field match "
            f"(presence={res['presence']}, confidence={res['confidence']}). "
            f"Top candidates: {alts}. Pass a `field` description or use Snapshot."
        )
    el = res["element"]
    device.click(el.coordinates.x, el.coordinates.y)
    device.sleep(0.3)
    device.set_fastinput_ime(enable=True)
    device.send_keys(text=text, clear=True)
    jev.mark_typed(el)
    return (
        f"Jev typed \"{text}\" into element {res['element_index']} '{el.name}' "
        f"(confidence={res['confidence']})"
    )


@mcp.tool(name='JevStep', description='Let Jev decide and execute ONE action toward a goal on the current screen (tap, type, scroll, back, home, enter, wait, done). One Jev call picks both the action and its target — far faster than reading a Snapshot yourself. Use JevRun to loop automatically.', annotations=ToolAnnotations(title="Jev Step", destructiveHint=True))
def jev_step_tool(goal: str, text_to_type: Optional[str] = None, context: Optional[str] = None):
    guard = _jev_unavailable()
    if guard:
        return guard
    device = require_device()
    try:
        res = jev.decide_step(goal, context=context, text_to_type=text_to_type)
    except Exception as e:
        return f"Jev decision failed: {e}"
    action = res["action"]
    if action == "done":
        return (f"JevStep: goal already achieved "
                f"(goal_achieved={res['goal_achieved']}).")
    if action == "give_up" or res.get("reason") == "no_elements":
        alts = _format_alts(res.get("alternatives", []))
        return (f"JevStep: cannot progress toward goal from this screen. "
                f"Action probabilities: {alts}. Take over with Snapshot.")
    try:
        detail = _jev_execute(device, res, text_to_type)
    except Exception as e:
        return f"JevStep: '{action}' execution failed: {e}"
    out = (f"JevStep → {action}: {detail} "
           f"(goal_achieved={res['goal_achieved']}, confidence={res['confidence']})")
    if res.get("needs_text") and res["needs_text"] >= 0.6 and text_to_type is None:
        out += (f"\nNote: Jev reports this screen likely needs text input "
                f"(needs_text={res['needs_text']}). Re-call with text_to_type=<text>.")
    return out


@mcp.tool(name='JevRun', description='Autonomously drive the device toward a goal: Jev repeatedly decides the next action (tap/type/scroll/back/enter/wait) and code executes it — each decision is a single ~0.3s call, far faster than an LLM reading Snapshots. Stops on done, give_up, or max_steps. If text may be needed, pass text_to_type; otherwise it pauses and asks you for the text. On give_up it returns context so you can take over with Snapshot.', annotations=ToolAnnotations(title="Jev Run", destructiveHint=True))
def jev_run_tool(goal: str, text_to_type: Optional[str] = None, max_steps: int = 8, context: Optional[str] = None):
    guard = _jev_unavailable()
    if guard:
        return guard
    device = require_device()
    log = []
    recent = []
    last_key = None
    repeats = 0
    prev_screen = None
    for step in range(max_steps):
        try:
            res = jev.decide_step(goal, context=context, text_to_type=text_to_type,
                                  recent_actions=recent)
        except Exception as e:
            log.append(f"{step + 1}. decision failed: {e}")
            return "JevRun stopped: Jev decision failed.\n" + "\n".join(log)

        action = res["action"]
        if action == "done":
            log.append(f"{step + 1}. done (goal_achieved={res['goal_achieved']})")
            return (f"JevRun finished: goal achieved in {step + 1} step(s).\n"
                    + "\n".join(log))
        if res.get("reason") == "no_elements":
            return ("JevRun stopped: no interactive elements on screen. "
                    "Take a Snapshot to see what is shown.\n" + "\n".join(log))
        if action == "give_up":
            alts = _format_alts(res.get("alternatives", []))
            return (f"JevRun stopped at step {step + 1}: Jev cannot progress "
                    f"toward the goal from this screen (probabilities: {alts}). "
                    "Take over with Snapshot or rephrase the goal.\n" + "\n".join(log))
        if res.get("needs_text") and res["needs_text"] >= 0.6 and text_to_type is None:
            return (f"JevRun paused at step {step + 1}: text input is required "
                    f"(needs_text={res['needs_text']}). Re-run with "
                    "text_to_type=<the text to enter>.\n" + "\n".join(log))

        key = (action, res.get("element_index"), res.get("screen_key"))
        if key == last_key:
            repeats += 1
            if repeats >= 2:
                return ("JevRun stopped: the same action repeated without the "
                        "screen changing (possible loop). Take over with Snapshot.\n"
                        + "\n".join(log))
        else:
            repeats = 0
        last_key = key

        try:
            detail = _jev_execute(device, res, text_to_type)
        except Exception as e:
            log.append(f"{step + 1}. {action} FAILED: {e}")
            return (f"JevRun stopped on execution error at step {step + 1}.\n"
                    + "\n".join(log))

        unchanged = prev_screen is not None and res.get("screen_key") == prev_screen
        note = f"{step + 1}. {action}: {detail}"
        if unchanged:
            note += " (screen unchanged)"
        log.append(note)
        recent.append(
            action
            + (f" on '{res['element'].name}'" if res.get("element") else "")
            + (" - screen unchanged" if unchanged else "")
        )
        prev_screen = res.get("screen_key")
        device.sleep(0.8)

    return (f"JevRun reached max_steps={max_steps} without a 'done' decision. "
            "Continue with JevRun/JevStep or take over with Snapshot.\n"
            + "\n".join(log))


@mcp.tool(name='JevCheck', description='Ask Jev a yes/no question about the current screen (e.g. "is the user logged in?", "did the message send?", "is an error dialog shown?"). Returns yes/no/uncertain with a probability — fast verification without reading a Snapshot.', annotations=ToolAnnotations(title="Jev Check", readOnlyHint=True))
def jev_check_tool(question: str):
    guard = _jev_unavailable()
    if guard:
        return guard
    require_device()
    try:
        res = jev.judge(question)
    except Exception as e:
        return f"Jev decision failed: {e}"
    return f"Jev verdict: {res['verdict']} (probability={res['probability']})"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
