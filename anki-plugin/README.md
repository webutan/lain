# Lain Discord Sync - Anki Plugin

Automatically sync vocabulary cards from the Lain Discord bot to Anki.

## Installation

### Method 1: Install from .ankiaddon file
1. Download `lain_sync.ankiaddon` from releases
2. Double-click the file to install, or drag it into Anki

### Method 2: Manual installation
1. Find your Anki addons folder:
   - Windows: `%APPDATA%\Anki2\addons21\`
   - Mac: `~/Library/Application Support/Anki2/addons21/`
   - Linux: `~/.local/share/Anki2/addons21/`
2. Create a folder called `lain_sync`
3. Copy `__init__.py` and `manifest.json` into that folder
4. Restart Anki

## Setup

1. In Discord, run `/anki_setup` to get your personal token
2. In Anki, go to **Tools → Lain Sync Settings...**
3. Enter:
   - **Server URL**: Your bot's server address (e.g., `http://your-server:8765`)
   - **Token**: Paste the token from Discord
   - **Deck Name**: Where cards will be added (default: "Lain Vocab")
   - **Sync Interval**: How often to check for new cards (default: 30 seconds)
4. Click **Test Connection** to verify it works
5. Click **Save**

## Usage

1. In Discord, save words with `/memo <word>`
2. Add memos to Anki queue with `/anki_add <number>` or `/anki_add all`
3. Keep Anki open - cards will sync automatically!
4. For manual sync: **Tools → Lain Sync Now**

## Commands

| Discord Command | Description |
|----------------|-------------|
| `/anki_setup` | Get your personal sync token |
| `/anki_add <#>` | Add a specific memo to sync queue |
| `/anki_add all` | Add all memos to sync queue |
| `/anki_pending` | View cards waiting to sync |
| `/anki_clear` | Clear pending cards |
| `/anki_reset` | Generate a new token |
