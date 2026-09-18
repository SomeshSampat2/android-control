# Android Control

A simple tool that lets AI assistants control Android devices. You can use it to automate apps, tap buttons, type text, and more - just by asking in plain English.

## Prerequisites

- **Python 3.13** - Required (install from python.org)
- **UV** - Python package manager (install: `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **ADB** - Android Debug Bridge (part of Android SDK Platform Tools)

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

**Fast Mode (Jev — recommended):**
- Describe a goal and Jev drives the device step-by-step (`JevRun`, `JevStep`)
- Tap elements by plain-English description (`JevTap`)
- Type into the right field automatically (`JevType`)
- Ask yes/no questions about the screen (`JevCheck`)

**Basic Actions:**
- Click, long press, swipe, and drag
- Press buttons (back, home, volume)
- Pull to refresh
- Read notifications
- See the screen state (`Snapshot`)

**App Control:**
- Launch any app
- Close apps
- See what app is running and wait for activities
- List installed apps
- Set screen orientation

**Advanced:**
- Copy and paste text (clipboard)
- Run shell commands
- Get device logs and device info

## Jev Fast Mode (Optional)

Android Control can use [Jev](https://typesafe.ai), TypeSafe's System One decision model, to make on-screen decisions in ~0.3s each — instead of your AI assistant reading a Snapshot and reasoning over it on every step. Jev never generates text; it picks among candidates the code supplies, so it can't hallucinate element names or coordinates.

- **JevTap** — "tap the search icon": Jev picks the element, code taps it
- **JevType** — you supply the text, Jev picks the field and code types
- **JevStep** — Jev decides and executes one action toward a goal
- **JevRun** — Jev loops decide→execute until the goal is done, it gives up, or it needs you to supply text (`needs_text`)
- **JevCheck** — yes/no questions about the screen ("is the user logged in?")
- **JevStatus** — check setup

To enable it, add your TypeSafe API key to the MCP server environment:

```json
{
  "mcpServers": {
    "android-control": {
      "command": "uvx",
      "args": ["--python", "3.13", "--from", "git+https://github.com/SomeshSampat2/android-control.git", "android-control"],
      "env": {
        "TYPESAFE_API_KEY": "your-key-here"
      }
    }
  }
}
```

Get a key at <https://console.typesafe.ai/settings/keys>. Optional env vars: `TYPESAFE_DEFAULT_MODEL` (default `jev-latest`) and `JEV_MAX_ELEMENTS` (default 40). If Jev isn't configured, all standard tools work exactly as before.

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
