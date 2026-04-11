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


def require_device():
    _connect_preferred_device()
    return mobile.get_device()

def _resolve_resource_id(device, resource_id: str) -> str:
    """Auto-expand short resourceId (e.g. 'btn_login') to full form (e.g. 'com.example.app:id/btn_login') using the current foreground app package."""
    if not resource_id or '/' in resource_id or ':' in resource_id:
        return resource_id
    try:
        pkg = device.app_current().get('package', '')
    except Exception:
        pkg = ''
    if pkg:
        return f'{pkg}:id/{resource_id}'
    return resource_id

@mcp.tool(name='ListDevices',description='List available ADB devices',annotations=ToolAnnotations(title="List Devices",readOnlyHint=True))
def list_devices_tool():
    devices=Mobile.list_devices()
    if not devices:
        return "No devices found. Ensure a device is connected and ADB is running."
    lines=[f"{serial}\t{state}" for serial,state in devices]
    return "\n".join(lines)

@mcp.tool(name='ConnectDevice',description='Connect to an ADB device by serial number',annotations=ToolAnnotations(title="Connect Device"))
def connect_device_tool(serial:str):
    target = Mobile.normalize_wifi_serial(serial) if ":" in serial else serial
    if ":" in target:
        Mobile.adb_connect(target)
    mobile.connect(target)
    return f'Connected to {target}'

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


@mcp.tool(name='ClickBySelector',description='Click on an element by selector (text, resourceId, className, description). More reliable than coordinate clicks — handles dynamic layouts and element reflow. At least one selector must be provided.',annotations=ToolAnnotations(title="Click By Selector",destructiveHint=True))
def click_by_selector_tool(text:str=None,resourceId:str=None,className:str=None,description:str=None,index:int=0,timeout:float=5.0):
    device=require_device()
    kwargs={}
    if text: kwargs['text']=text
    if resourceId: kwargs['resourceId']=_resolve_resource_id(device, resourceId)
    if className: kwargs['className']=className
    if description: kwargs['description']=description
    if not kwargs:
        return 'Error: at least one selector (text, resourceId, className, description) must be provided'
    if index: kwargs['index']=index
    el=device(**kwargs)
    if not el.wait(timeout=timeout):
        return f'Element not found with selectors {kwargs} within {timeout}s'
    el.click()
    return f'Clicked element matching {kwargs}'

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
    name="Type",
    description="Type on a specific cordinate",
    annotations=ToolAnnotations(title="Type", destructiveHint=True),
)
def type_tool(text: str, x: int, y: int, clear: bool = False):
    device = require_device()
    device.set_fastinput_ime(enable=True)
    device.send_keys(text=text, clear=clear)
    return f'Typed "{text}" on ({x},{y})'


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
    name="Press",
    description="Press on specific button on the device",
    annotations=ToolAnnotations(title="Press", destructiveHint=True),
)
def press_tool(button: str):
    device = require_device()
    device.press(button)
    return f'Pressed the "{button}" button'


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


@mcp.tool(name='WaitForElement',description='Wait for an element to appear on screen. Use this instead of Wait when content is loading dynamically. Returns element info when found or error on timeout.',annotations=ToolAnnotations(title="Wait For Element",readOnlyHint=True))
def wait_for_element_tool(text:str=None,resourceId:str=None,className:str=None,description:str=None,timeout:float=10.0):
    device=require_device()
    kwargs={}
    if text: kwargs['text']=text
    if resourceId: kwargs['resourceId']=_resolve_resource_id(device, resourceId)
    if className: kwargs['className']=className
    if description: kwargs['description']=description
    if not kwargs:
        return 'Error: at least one selector (text, resourceId, className, description) must be provided'
    el=device(**kwargs)
    if el.wait(timeout=timeout):
        info=el.info
        bounds=info.get('bounds',{})
        cx=(bounds.get('left',0)+bounds.get('right',0))//2
        cy=(bounds.get('top',0)+bounds.get('bottom',0))//2
        return f'Element found: text="{info.get("text","")}" class={info.get("className","")} coords=({cx},{cy}) bounds=[{bounds.get("left",0)},{bounds.get("top",0)}][{bounds.get("right",0)},{bounds.get("bottom",0)}]'
    return f'Element not found with selectors {kwargs} within {timeout}s'


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


# Advanced UI Tools
@mcp.tool(name='Scroll', description='Scroll in a specific direction (up, down, left, right)', annotations=ToolAnnotations(title="Scroll", destructiveHint=True))
def scroll_tool(direction: str, distance: int = 500):
    try:
        device = require_device()
        if direction == "down":
            device.swipe(540, 1500, 540, 1500 - distance)
        elif direction == "up":
            device.swipe(540, 1500 - distance, 540, 1500)
        elif direction == "left":
            device.swipe(1000, 1000, 1000 - distance, 1000)
        elif direction == "right":
            device.swipe(1000 - distance, 1000, 1000, 1000)
        else:
            return f"Invalid direction: {direction}. Use: up, down, left, right"
        return f"Scrolled {direction}"
    except Exception as e:
        return f"Error scrolling: {str(e)}"

@mcp.tool(name='MultiTap', description='Tap multiple times rapidly at a location', annotations=ToolAnnotations(title="Multi Tap", destructiveHint=True))
def multi_tap_tool(x: int, y: int, count: int = 2):
    try:
        device = require_device()
        for _ in range(count):
            device.click(x, y)
            device.sleep(0.1)
        return f"Tapped {count} times at ({x}, {y})"
    except Exception as e:
        return f"Error multi-tapping: {str(e)}"

@mcp.tool(name='ScrollToElement', description='Scroll until an element is found', annotations=ToolAnnotations(title="Scroll To Element", destructiveHint=True))
def scroll_to_element_tool(text: str = None, resourceId: str = None, max_scrolls: int = 5):
    try:
        device = require_device()
        kwargs = {}
        if text: kwargs['text'] = text
        if resourceId: kwargs['resourceId'] = _resolve_resource_id(device, resourceId)
        if not kwargs:
            return 'Error: at least one selector (text or resourceId) must be provided'
        
        for i in range(max_scrolls):
            el = device(**kwargs)
            if el.wait(timeout=1):
                return f"Element found after {i} scrolls"
            device.swipe(540, 1500, 540, 1000)  # Scroll down
            device.sleep(0.5)
        
        return f"Element not found after {max_scrolls} scrolls"
    except Exception as e:
        return f"Error scrolling to element: {str(e)}"


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
@mcp.tool(name='ToggleWiFi', description='Turn WiFi on or off', annotations=ToolAnnotations(title="Toggle WiFi", destructiveHint=True))
def toggle_wifi_tool(state: str):
    try:
        if state not in ["on", "off"]:
            return "Error: state must be 'on' or 'off'"
        subprocess.run(['adb', 'shell', 'svc', 'wifi', state], timeout=15)
        return f"WiFi turned {state}"
    except Exception as e:
        return f"Error toggling WiFi: {str(e)}"

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


# Advanced Features
@mcp.tool(name='GetElementParent', description='Get parent of an element by selector', annotations=ToolAnnotations(title="Get Element Parent", readOnlyHint=True))
def get_element_parent_tool(text: str = None, resourceId: str = None):
    try:
        device = require_device()
        kwargs = {}
        if text: kwargs['text'] = text
        if resourceId: kwargs['resourceId'] = _resolve_resource_id(device, resourceId)
        if not kwargs:
            return 'Error: at least one selector (text or resourceId) must be provided'
        
        el = device(**kwargs)
        if not el.wait(timeout=5):
            return f"Element not found with selectors {kwargs}"
        
        parent = el.parent
        if parent:
            parent_info = parent.info
            return f"Parent: text='{parent_info.get('text')}' class={parent_info.get('className')}"
        return "No parent found"
    except Exception as e:
        return f"Error getting element parent: {str(e)}"

@mcp.tool(name='VerifyText', description='Verify if text exists on screen', annotations=ToolAnnotations(title="Verify Text", readOnlyHint=True))
def verify_text_tool(text: str, should_exist: bool = True):
    try:
        device = require_device()
        xml = device.dump_hierarchy()
        exists = text in xml
        if should_exist:
            return f"Text '{text}' {'found' if exists else 'not found'}"
        else:
            return f"Text '{text}' {'not found' if not exists else 'found (unexpected)'}"
    except Exception as e:
        return f"Error verifying text: {str(e)}"

@mcp.tool(name='GetElementInfo', description='Get detailed information about an element by selector', annotations=ToolAnnotations(title="Get Element Info", readOnlyHint=True))
def get_element_info_tool(text: str = None, resourceId: str = None):
    try:
        device = require_device()
        kwargs = {}
        if text: kwargs['text'] = text
        if resourceId: kwargs['resourceId'] = _resolve_resource_id(device, resourceId)
        if not kwargs:
            return 'Error: at least one selector (text or resourceId) must be provided'
        
        el = device(**kwargs)
        if not el.wait(timeout=5):
            return f"Element not found with selectors {kwargs}"
        
        info = el.info
        output = f"Text: {info.get('text')}\n"
        output += f"Class: {info.get('className')}\n"
        output += f"Resource ID: {info.get('resourceId')}\n"
        output += f"Clickable: {info.get('clickable')}\n"
        output += f"Enabled: {info.get('enabled')}\n"
        output += f"Bounds: {info.get('bounds')}\n"
        return output
    except Exception as e:
        return f"Error getting element info: {str(e)}"

@mcp.tool(name='SwipeElement', description='Swipe on a specific element', annotations=ToolAnnotations(title="Swipe Element", destructiveHint=True))
def swipe_element_tool(text: str = None, resourceId: str = None, direction: str = "down", distance: int = 500):
    try:
        device = require_device()
        kwargs = {}
        if text: kwargs['text'] = text
        if resourceId: kwargs['resourceId'] = _resolve_resource_id(device, resourceId)
        if not kwargs:
            return 'Error: at least one selector (text or resourceId) must be provided'
        
        el = device(**kwargs)
        if not el.wait(timeout=5):
            return f"Element not found with selectors {kwargs}"
        
        bounds = el.info.get('bounds', {})
        x = (bounds.get('left', 0) + bounds.get('right', 0)) // 2
        y = (bounds.get('top', 0) + bounds.get('bottom', 0)) // 2
        
        if direction == "down":
            device.swipe(x, y, x, y + distance)
        elif direction == "up":
            device.swipe(x, y, x, y - distance)
        elif direction == "left":
            device.swipe(x, y, x - distance, y)
        elif direction == "right":
            device.swipe(x, y, x + distance, y)
        else:
            return f"Invalid direction: {direction}. Use: up, down, left, right"
        
        return f"Swiped {direction} on element"
    except Exception as e:
        return f"Error swiping element: {str(e)}"

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


# Specialized Tools
@mcp.tool(name='GetBatteryInfo', description='Get battery information', annotations=ToolAnnotations(title="Get Battery Info", readOnlyHint=True))
def get_battery_info_tool():
    try:
        result = subprocess.run(['adb', 'shell', 'dumpsys', 'battery'], capture_output=True, text=True, timeout=10)
        return result.stdout
    except Exception as e:
        return f"Error getting battery info: {str(e)}"

@mcp.tool(name='TakeScreenshot', description='Take a screenshot and save to device', annotations=ToolAnnotations(title="Take Screenshot", destructiveHint=True))
def take_screenshot_tool(filename: str = "screenshot.png"):
    try:
        device = require_device()
        device.screenshot(filename)
        return f"Screenshot saved to {filename}"
    except Exception as e:
        return f"Error taking screenshot: {str(e)}"

@mcp.tool(name='GetNetworkInfo', description='Get network connection information', annotations=ToolAnnotations(title="Get Network Info", readOnlyHint=True))
def get_network_info_tool():
    try:
        result = subprocess.run(['adb', 'shell', 'dumpsys', 'connectivity'], capture_output=True, text=True, timeout=10)
        return result.stdout[:2000]  # Limit output
    except Exception as e:
        return f"Error getting network info: {str(e)}"


# Network Inspection Tools
@mcp.tool(name='GetAPILogs', description='Get logs related to API calls from the app. Filters logcat for HTTP requests, network operations, and API calls.', annotations=ToolAnnotations(title="Get API Logs", readOnlyHint=True))
def get_api_logs_tool(package_name: str = None, lines: int = 100):
    try:
        # Filter for network-related logs
        filters = [
            'okhttp',
            'http',
            'HttpURLConnection',
            'Retrofit',
            'Volley',
            'API',
            'network',
            'request',
            'response'
        ]
        
        cmd = ['adb', 'logcat', '-d', '-v', 'time']
        if package_name:
            cmd.extend(['|', 'grep', package_name])
        else:
            # Add grep for network-related keywords
            filter_str = '|'.join(filters)
            cmd.extend(['|', 'grep', '-i', f'-E', f'({filter_str})'])
        
        cmd.extend(['|', 'tail', '-n', str(lines)])
        
        result = subprocess.run(' '.join(cmd), shell=True, capture_output=True, text=True, timeout=30)
        
        if not result.stdout:
            return "No API logs found. Make sure the app is making network requests and debug logging is enabled."
        
        return result.stdout
    except Exception as e:
        return f"Error getting API logs: {str(e)}"

@mcp.tool(name='StartAPILogger', description='Start logging API requests/responses by clearing logs and preparing for capture', annotations=ToolAnnotations(title="Start API Logger", destructiveHint=True))
def start_api_logger_tool(package_name: str = None):
    try:
        # Clear logcat to start fresh
        subprocess.run(['adb', 'logcat', '-c'], timeout=10)
        
        # Enable verbose logging for network operations if possible
        if package_name:
            # Set log level for specific package
            subprocess.run(['adb', 'shell', 'setprop', 'log.tag.okhttp', 'DEBUG'], timeout=5)
            subprocess.run(['adb', 'shell', 'setprop', 'log.tag.Retrofit', 'DEBUG'], timeout=5)
        
        return "API logger started. Navigate to the page and use StopAPILogger to capture the API calls."
    except Exception as e:
        return f"Error starting API logger: {str(e)}"

@mcp.tool(name='StopAPILogger', description='Stop logging API requests/responses and return captured data', annotations=ToolAnnotations(title="Stop API Logger", readOnlyHint=True))
def stop_api_logger_tool(package_name: str = None, lines: int = 200):
    try:
        filters = [
            'okhttp',
            'http',
            'HttpURLConnection',
            'Retrofit',
            'Volley',
            'API',
            'network',
            'request',
            'response',
            'url',
            'endpoint'
        ]
        
        cmd = ['adb', 'logcat', '-d', '-v', 'time']
        if package_name:
            cmd.extend(['|', 'grep', package_name])
        else:
            filter_str = '|'.join(filters)
            cmd.extend(['|', 'grep', '-i', f'-E', f'({filter_str})'])
        
        cmd.extend(['|', 'tail', '-n', str(lines)])
        
        result = subprocess.run(' '.join(cmd), shell=True, capture_output=True, text=True, timeout=30)
        
        if not result.stdout:
            return "No API logs captured. Make sure network requests were made after starting the logger."
        
        # Parse and format the output
        lines_list = result.stdout.split('\n')
        api_calls = []
        
        for line in lines_list:
            if any(keyword.lower() in line.lower() for keyword in ['http', 'url', 'request', 'response', 'endpoint']):
                api_calls.append(line)
        
        output = "=== Captured API Calls ===\n"
        output += f"Total API-related log entries: {len(api_calls)}\n\n"
        output += "\n".join(api_calls[:50])  # Limit to 50 entries
        
        return output
    except Exception as e:
        return f"Error stopping API logger: {str(e)}"

@mcp.tool(name='GetAPIResponse', description='Extract response data from API logs. Use after StopAPILogger to get specific response details.', annotations=ToolAnnotations(title="Get API Response", readOnlyHint=True))
def get_api_response_tool(package_name: str = None, search_term: str = None):
    try:
        cmd = ['adb', 'logcat', '-d', '-v', 'time']
        
        if package_name:
            cmd.extend(['|', 'grep', package_name])
        
        if search_term:
            cmd.extend(['|', 'grep', '-i', search_term])
        
        result = subprocess.run(' '.join(cmd), shell=True, capture_output=True, text=True, timeout=30)
        
        if not result.stdout:
            return "No response data found. Make sure API calls were made and use StopAPILogger first."
        
        # Filter for response-related logs
        lines_list = result.stdout.split('\n')
        responses = []
        
        for line in lines_list:
            if any(keyword.lower() in line.lower() for keyword in ['response', '200', '201', 'error', 'fail', 'success']):
                responses.append(line)
        
        output = "=== API Response Data ===\n"
        output += "\n".join(responses[:30])  # Limit to 30 entries
        
        return output
    except Exception as e:
        return f"Error getting API response: {str(e)}"

@mcp.tool(name='SetProxy', description='Set HTTP/HTTPS proxy for device to intercept API traffic (requires proxy server like Charles/Fiddler)', annotations=ToolAnnotations(title="Set Proxy", destructiveHint=True))
def set_proxy_tool(host: str, port: int):
    try:
        # Set global HTTP proxy
        subprocess.run(['adb', 'shell', 'settings', 'put', 'global', 'http_proxy', f'{host}:{port}'], timeout=10)
        subprocess.run(['adb', 'shell', 'settings', 'put', 'global', 'https_proxy', f'{host}:{port}'], timeout=10)
        
        return f"Proxy set to {host}:{port}. Note: This may require additional SSL certificate installation for HTTPS traffic."
    except Exception as e:
        return f"Error setting proxy: {str(e)}"

@mcp.tool(name='ClearProxy', description='Clear HTTP/HTTPS proxy settings from device', annotations=ToolAnnotations(title="Clear Proxy", destructiveHint=True))
def clear_proxy_tool():
    try:
        subprocess.run(['adb', 'shell', 'settings', 'put', 'global', 'http_proxy', ':0'], timeout=10)
        subprocess.run(['adb', 'shell', 'settings', 'put', 'global', 'https_proxy', ':0'], timeout=10)
        
        return "Proxy settings cleared."
    except Exception as e:
        return f"Error clearing proxy: {str(e)}"

def main():
    mcp.run()


if __name__ == "__main__":
    main()
