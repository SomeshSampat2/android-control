# Android Control

A simple tool that lets AI assistants control Android devices. You can use it to automate apps, tap buttons, type text, and more - just by asking in plain English.

## What You Need

- Python 3.13
- ADB (Android Debug Bridge)
- An Android device or emulator (Android 10+)
- UV package manager

## How to Install

### Step 1: Install UV

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Step 2: Connect Your Android Device

Connect your phone via USB or start an emulator, then run:

```bash
adb devices
```

You should see your device listed. If not, enable USB debugging in your phone's developer settings.

### Step 3: Configure Your IDE

This MCP server works with any IDE that supports the Model Context Protocol (MCP), including:

- **Windsurf** - Modern AI-powered code editor
- **Cursor** - AI-first code editor
- **Claude Desktop** - Anthropic's official Claude app
- **Antigravity** - AI development environment
- **Kiro Ide** - AI-enhanced development environment
- **Codex** - AI-powered coding assistant
- **Any other MCP-compatible IDE**

**For Windsurf, Cursor, or Claude Desktop:**

Find your MCP config file:
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Mac: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windsurf/Cursor may use similar paths in their config directories

Add this to your MCP config:

```json
{
  "mcpServers": {
    "android-control": {
      "command": "uvx",
      "args": [
        "--python",
        "3.13",
        "--from",
        "git+https://github.com/SomeshSampat2/android-control.git",
        "android-control"
      ]
    }
  }
}
```

**For other IDEs:**
Check your IDE's documentation for MCP configuration. Most follow a similar JSON-based configuration format.

### Step 4: Restart Your IDE

Restart your IDE to load the MCP server. You should now be able to control your Android device with AI assistance.

## What You Can Do

AI assistants can use these tools to control your phone:

**Basic Actions:**
- Click, tap, and long press on screen elements
- Type text into input fields
- Swipe and scroll
- Press buttons (back, home, volume)
- Take screenshots

**App Control:**
- Launch any app
- Close apps
- See what app is running
- List installed apps

**Advanced:**
- Find and click elements by text or ID
- Scroll until finding specific elements
- Pull to refresh
- Wait for elements to appear
- Read notifications
- Copy and paste text
- Run shell commands
- Get device logs
- Check battery and network status
- Inspect API calls from apps

**Network Debugging:**
- See what APIs apps are calling
- Capture HTTP requests and responses
- Set up proxy to intercept network traffic

## WiFi Connection (Optional)

If you want to connect over WiFi instead of USB:

```json
{
  "mcpServers": {
    "android-control": {
      "command": "uvx",
      "args": [
        "--python",
        "3.13",
        "--from",
        "git+https://github.com/SomeshSampat2/android-control.git",
        "android-control"
      ],
      "env": {
        "ANDROID_MCP_CONNECTION": "wifi",
        "ANDROID_MCP_HOST": "192.168.1.3"
      }
    }
  }
}
```

## Safety

This tool can control your phone. Use it carefully, especially with untrusted AI assistants. It's best to test on an emulator or spare device first.

## License

Apache License 2.0 - free to use for any purpose.

## Inspired By

This project was inspired by [Android-MCP](https://github.com/CursorTouch/Android-MCP) by CursorTouch.
