# Lain Discord Sync - Anki Plugin

Automatically sync vocabulary cards from the Lain Discord bot to Anki, and track your study streaks!

## Features

- **Auto-sync cards** from Discord to Anki
- **Streak tracking** - Track which decks you're studying
- **Daily reminders** - Get pinged in Discord if you haven't finished your cards
- **Leaderboards** - Compete with others on streaks and new cards learned

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
3. **Connection tab:**
   - **Server URL**: `https://anki.iwakura.online`
   - **Token**: Paste the token from Discord
   - **Import Deck**: Where cards will be added (default: "Lain Vocab")
   - **Sync Interval**: How often to check for new cards (default: 30 seconds)
4. **Streak Tracking tab:**
   - Select which decks to track for your streak
   - Enable daily reminder and set your preferred time
5. Click **Test Connection** to verify it works
6. Click **Save**

## Streak Tracking

To maintain your streak:
1. Select the decks you want to track in the Streak Tracking tab
2. Complete all due cards in those decks each day
3. Your streak will reset if you have cards remaining at the end of the day

### Daily Reminders
- Enable reminders to get pinged in Discord at your chosen time
- Make sure you have the **Anki Reminder** role in Discord (from the role selection menu)
- Only triggers if you still have cards due

### Leaderboard
- Daily leaderboard posted at 11:55 PM JST
- Shows streak leaders and who learned the most new cards
- Check your own streak anytime with `/anki_streak` in Discord

## Usage

1. In Discord, save words with `/memo <word>`
2. Add memos to Anki queue with `/anki_add <number>` or `/anki_add all`
3. Keep Anki open - cards will sync automatically!
4. For manual sync: **Tools → Lain Sync Now**
5. Check your streak: `/anki_streak` in Discord

## Discord Commands

| Command | Description |
|---------|-------------|
| `/anki_setup` | Get your personal sync token |
| `/anki_add <#>` | Add a specific memo to sync queue |
| `/anki_add all` | Add all memos to sync queue |
| `/anki_pending` | View cards waiting to sync |
| `/anki_clear` | Clear pending cards |
| `/anki_reset` | Generate a new token |
| `/anki_streak` | Check your study streak and stats |

## Troubleshooting

**Cards not syncing?**
- Make sure your token is correct (try `/anki_reset` for a new one)
- Check that the server URL is `https://anki.iwakura.online`
- Click "Test Connection" to verify

**Streak not updating?**
- Make sure you've selected decks to track
- Stats are sent every 5 minutes, so wait a bit
- Click "Lain Sync Now" to force an update

**Not getting reminders?**
- Enable reminders in the Streak Tracking tab
- Make sure you have the Anki Reminder role in Discord
- Keep Anki open so it can report your stats
