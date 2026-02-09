# Lain Discord Bot - Anki Sync Plugin
# Syncs vocabulary cards from Discord to Anki and reports study stats

import json
import os
import time as time_module
from datetime import datetime
from typing import Optional, List
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

from aqt import mw, gui_hooks
from aqt.qt import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QMessageBox, QTimer,
    QListWidget, QListWidgetItem, QTimeEdit, QGroupBox,
    QCheckBox, QTime, Qt, QTabWidget, QWidget
)
from aqt.utils import showInfo, showWarning
from anki.notes import Note

# Plugin configuration
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.json')
DEFAULT_CONFIG = {
    'server_url': '',
    'token': '',
    'deck_name': 'Lain Vocab',
    'sync_interval': 30,  # seconds
    'stats_interval': 300,  # 5 minutes
    'enabled': True,
    'tracked_decks': [],
    'reminder_time': None,  # HH:MM format
    'reminder_enabled': False,
}


def load_config():
    """Load plugin configuration"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # Merge with defaults for any missing keys
                return {**DEFAULT_CONFIG, **config}
        except:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config):
    """Save plugin configuration"""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)


def get_all_deck_names() -> List[str]:
    """Get all deck names from Anki"""
    if not mw.col:
        return []
    decks = mw.col.decks.all_names_and_ids()
    return [d.name for d in decks if '::' not in d.name]  # Skip subdecks for simplicity


def get_deck_stats(deck_names: List[str]) -> dict:
    """Get study stats for specific decks"""
    if not mw.col or not deck_names:
        return {'due': 0, 'new': 0, 'reviewed': 0, 'time_today': 0, 'time_total': 0}

    total_due = 0
    total_new = 0

    # Get counts for each tracked deck using Anki's scheduler
    for deck_name in deck_names:
        deck_id = mw.col.decks.id_for_name(deck_name)
        if deck_id:
            try:
                # Select the deck temporarily to get its counts
                original_deck = mw.col.decks.current()['id']
                mw.col.decks.select(deck_id)

                # Get counts: (new, learning, review)
                counts = mw.col.sched.counts()
                total_new += counts[0]
                total_due += counts[1] + counts[2]  # learning + review

                # Restore original deck
                mw.col.decks.select(original_deck)
            except Exception as e:
                print(f"Lain Sync: Error getting counts for {deck_name}: {e}")

    # Get today's stats from review log
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_timestamp = int(today_start.timestamp() * 1000)

    # Count reviews done today
    reviewed_today = mw.col.db.scalar(
        "SELECT COUNT() FROM revlog WHERE id > ?",
        today_timestamp
    ) or 0

    # Get time studied today (in milliseconds, convert to seconds)
    time_today_ms = mw.col.db.scalar(
        "SELECT SUM(time) FROM revlog WHERE id > ?",
        today_timestamp
    ) or 0
    time_today = time_today_ms // 1000  # Convert to seconds

    # Get total time studied all-time (in seconds)
    time_total_ms = mw.col.db.scalar(
        "SELECT SUM(time) FROM revlog"
    ) or 0
    time_total = time_total_ms // 1000  # Convert to seconds

    print(f"Lain Sync Stats: due={total_due}, new={total_new}, reviewed={reviewed_today}, time_today={time_today}s, time_total={time_total}s")

    return {
        'due': total_due,
        'new': total_new,
        'reviewed': reviewed_today,
        'time_today': time_today,
        'time_total': time_total
    }


def get_timezone_offset() -> int:
    """Get local timezone offset from UTC in hours"""
    local_time = datetime.now()
    utc_time = datetime.utcnow()
    diff = local_time - utc_time
    return round(diff.total_seconds() / 3600)


class LainSyncConfig(QDialog):
    """Configuration dialog for Lain Sync with tabs"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = load_config()
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("Lain Sync Settings")
        self.setMinimumWidth(500)
        self.setMinimumHeight(500)

        layout = QVBoxLayout()

        # Create tab widget
        tabs = QTabWidget()

        # Connection tab
        connection_tab = QWidget()
        connection_layout = QVBoxLayout(connection_tab)
        self.setup_connection_tab(connection_layout)
        tabs.addTab(connection_tab, "Connection")

        # Streak Tracking tab
        streak_tab = QWidget()
        streak_layout = QVBoxLayout(streak_tab)
        self.setup_streak_tab(streak_layout)
        tabs.addTab(streak_tab, "Streak Tracking")

        layout.addWidget(tabs)

        # Buttons
        btn_layout = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.save_settings)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def setup_connection_tab(self, layout):
        """Set up the connection settings tab"""
        # Server URL
        url_group = QGroupBox("Server Connection")
        url_layout = QVBoxLayout()

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("Server URL:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://anki.iwakura.online")
        self.url_input.setText(self.config.get('server_url', ''))
        url_row.addWidget(self.url_input)
        url_layout.addLayout(url_row)

        token_row = QHBoxLayout()
        token_row.addWidget(QLabel("Your Token:"))
        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText("Paste your token from /anki_setup")
        self.token_input.setText(self.config.get('token', ''))
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        token_row.addWidget(self.token_input)
        url_layout.addLayout(token_row)

        # Show/hide token button
        self.show_token_btn = QPushButton("Show Token")
        self.show_token_btn.clicked.connect(self.toggle_token_visibility)
        url_layout.addWidget(self.show_token_btn)

        # Test connection button
        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self.test_connection)
        url_layout.addWidget(test_btn)

        url_group.setLayout(url_layout)
        layout.addWidget(url_group)

        # Card Sync Settings
        sync_group = QGroupBox("Card Sync Settings")
        sync_layout = QVBoxLayout()

        deck_row = QHBoxLayout()
        deck_row.addWidget(QLabel("Import Deck:"))
        self.deck_input = QLineEdit()
        self.deck_input.setText(self.config.get('deck_name', 'Lain Vocab'))
        self.deck_input.setToolTip("Deck where cards from Discord will be added")
        deck_row.addWidget(self.deck_input)
        sync_layout.addLayout(deck_row)

        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("Sync Interval (seconds):"))
        self.interval_input = QSpinBox()
        self.interval_input.setRange(10, 300)
        self.interval_input.setValue(self.config.get('sync_interval', 30))
        interval_row.addWidget(self.interval_input)
        sync_layout.addLayout(interval_row)

        sync_group.setLayout(sync_layout)
        layout.addWidget(sync_group)

        layout.addStretch()

    def setup_streak_tab(self, layout):
        """Set up the streak tracking tab"""
        # Deck selection
        deck_group = QGroupBox("Track These Decks for Streak")
        deck_layout = QVBoxLayout()

        deck_layout.addWidget(QLabel("Select decks to track (you must complete all due cards to keep streak):"))

        self.deck_list = QListWidget()
        self.deck_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)

        # Populate with all decks
        tracked_decks = self.config.get('tracked_decks', [])
        for deck_name in get_all_deck_names():
            item = QListWidgetItem(deck_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if deck_name in tracked_decks else Qt.CheckState.Unchecked)
            self.deck_list.addItem(item)

        deck_layout.addWidget(self.deck_list)

        # Refresh button
        refresh_btn = QPushButton("Refresh Deck List")
        refresh_btn.clicked.connect(self.refresh_deck_list)
        deck_layout.addWidget(refresh_btn)

        deck_group.setLayout(deck_layout)
        layout.addWidget(deck_group)

        # Reminder settings
        reminder_group = QGroupBox("Daily Reminder")
        reminder_layout = QVBoxLayout()

        self.reminder_enabled = QCheckBox("Enable daily reminder")
        self.reminder_enabled.setChecked(self.config.get('reminder_enabled', False))
        self.reminder_enabled.setToolTip("Get pinged in Discord if you haven't finished your cards")
        reminder_layout.addWidget(self.reminder_enabled)

        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("Reminder Time:"))
        self.reminder_time = QTimeEdit()
        reminder_time_str = self.config.get('reminder_time', '20:00')
        if reminder_time_str:
            try:
                hour, minute = map(int, reminder_time_str.split(':'))
                self.reminder_time.setTime(QTime(hour, minute))
            except:
                self.reminder_time.setTime(QTime(20, 0))
        else:
            self.reminder_time.setTime(QTime(20, 0))
        self.reminder_time.setDisplayFormat("HH:mm")
        time_row.addWidget(self.reminder_time)
        reminder_layout.addLayout(time_row)

        reminder_layout.addWidget(QLabel("Note: Make sure you have the Anki Reminder role in Discord!"))

        reminder_group.setLayout(reminder_layout)
        layout.addWidget(reminder_group)

        # Current stats display
        stats_group = QGroupBox("Current Stats (Tracked Decks)")
        stats_layout = QVBoxLayout()

        self.stats_label = QLabel("Loading...")
        stats_layout.addWidget(self.stats_label)

        refresh_stats_btn = QPushButton("Refresh Stats")
        refresh_stats_btn.clicked.connect(self.refresh_stats)
        stats_layout.addWidget(refresh_stats_btn)

        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)

        # Refresh stats on load
        self.refresh_stats()

    def refresh_deck_list(self):
        """Refresh the deck list"""
        current_checked = self.get_tracked_decks()
        self.deck_list.clear()

        for deck_name in get_all_deck_names():
            item = QListWidgetItem(deck_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if deck_name in current_checked else Qt.CheckState.Unchecked)
            self.deck_list.addItem(item)

    def refresh_stats(self):
        """Refresh the stats display"""
        tracked = self.get_tracked_decks()
        if tracked:
            stats = get_deck_stats(tracked)
            self.stats_label.setText(
                f"Due: {stats['due']} cards\n"
                f"New available: {stats['new']} cards\n"
                f"Reviewed today: {stats['reviewed']} cards\n"
                f"Status: {'✅ Complete!' if stats['due'] == 0 else '⏳ Cards remaining'}"
            )
        else:
            self.stats_label.setText("No decks selected. Select decks above to track.")

    def get_tracked_decks(self) -> List[str]:
        """Get list of checked deck names"""
        tracked = []
        for i in range(self.deck_list.count()):
            item = self.deck_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                tracked.append(item.text())
        return tracked

    def toggle_token_visibility(self):
        if self.token_input.echoMode() == QLineEdit.EchoMode.Password:
            self.token_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.show_token_btn.setText("Hide Token")
        else:
            self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_token_btn.setText("Show Token")

    def test_connection(self):
        server_url = self.url_input.text().strip().rstrip('/')
        token = self.token_input.text().strip()

        if not server_url or not token:
            showWarning("Please enter both server URL and token.")
            return

        try:
            url = f"{server_url}/anki/cards?token={token}"
            req = Request(url)
            req.add_header('User-Agent', 'LainAnkiSync/2.0')

            with urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))

                if 'error' in data:
                    showWarning(f"Server error: {data['error']}")
                else:
                    cards = data.get('cards', [])
                    showInfo(f"Connection successful!\n{len(cards)} card(s) pending.")

        except HTTPError as e:
            showWarning(f"HTTP Error: {e.code} - {e.reason}")
        except URLError as e:
            showWarning(f"Connection failed: {e.reason}")
        except Exception as e:
            showWarning(f"Error: {str(e)}")

    def save_settings(self):
        self.config['server_url'] = self.url_input.text().strip().rstrip('/')
        self.config['token'] = self.token_input.text().strip()
        self.config['deck_name'] = self.deck_input.text().strip() or 'Lain Vocab'
        self.config['sync_interval'] = self.interval_input.value()
        self.config['tracked_decks'] = self.get_tracked_decks()
        self.config['reminder_enabled'] = self.reminder_enabled.isChecked()
        self.config['reminder_time'] = self.reminder_time.time().toString("HH:mm")

        save_config(self.config)

        # Restart timers with new settings
        restart_sync_timer()
        restart_stats_timer()

        # Send config to server
        send_config_to_server()

        showInfo("Settings saved! Sync will use the new settings.")
        self.accept()


# Global timers
sync_timer: Optional[QTimer] = None
stats_timer: Optional[QTimer] = None


def get_or_create_deck(deck_name: str) -> int:
    """Get deck ID, creating it if it doesn't exist"""
    deck_id = mw.col.decks.id(deck_name)
    return deck_id


def get_or_create_note_type():
    """Get or create the Lain Vocab note type"""
    model_name = "Lain Vocab"
    model = mw.col.models.by_name(model_name)

    if model is None:
        # Create new model
        model = mw.col.models.new(model_name)

        # Add fields
        front_field = mw.col.models.new_field("Front")
        mw.col.models.add_field(model, front_field)

        back_field = mw.col.models.new_field("Back")
        mw.col.models.add_field(model, back_field)

        # Add template
        template = mw.col.models.new_template("Card 1")
        template['qfmt'] = '{{Front}}'
        template['afmt'] = '{{FrontSide}}<hr id="answer">{{Back}}'
        mw.col.models.add_template(model, template)

        # Add the model
        mw.col.models.add(model)

    return model


def add_card(front: str, back: str, deck_name: str) -> bool:
    """Add a card to Anki"""
    try:
        model = get_or_create_note_type()
        deck_id = get_or_create_deck(deck_name)

        note = Note(mw.col, model)
        note['Front'] = front
        note['Back'] = back
        note.note_type()['did'] = deck_id

        mw.col.add_note(note, deck_id)
        return True
    except Exception as e:
        print(f"Lain Sync: Error adding card: {e}")
        return False


def fetch_and_sync_cards():
    """Fetch pending cards from server and add them to Anki"""
    config = load_config()

    if not config.get('enabled', True):
        return

    server_url = config.get('server_url', '').strip().rstrip('/')
    token = config.get('token', '').strip()
    deck_name = config.get('deck_name', 'Lain Vocab')

    if not server_url or not token:
        return

    try:
        # Fetch cards
        url = f"{server_url}/anki/cards?token={token}"
        req = Request(url)
        req.add_header('User-Agent', 'LainAnkiSync/2.0')

        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))

        if 'error' in data:
            print(f"Lain Sync: Server error - {data['error']}")
            return

        cards = data.get('cards', [])

        if not cards:
            return

        # Add cards to Anki
        added_ids = []
        for card in cards:
            front = card.get('front', '')
            back = card.get('back', '')
            card_id = card.get('id', '')

            if front and add_card(front, back, deck_name):
                added_ids.append(card_id)

        if added_ids:
            # Confirm cards were added
            confirm_url = f"{server_url}/anki/confirm?token={token}"
            confirm_data = json.dumps({'card_ids': added_ids}).encode('utf-8')

            confirm_req = Request(confirm_url, data=confirm_data)
            confirm_req.add_header('Content-Type', 'application/json')
            confirm_req.add_header('User-Agent', 'LainAnkiSync/2.0')

            with urlopen(confirm_req, timeout=10) as response:
                pass

            # Refresh the display
            mw.reset()

            print(f"Lain Sync: Added {len(added_ids)} card(s)")

    except HTTPError as e:
        print(f"Lain Sync: HTTP Error - {e.code}")
    except URLError as e:
        print(f"Lain Sync: Connection error - {e.reason}")
    except Exception as e:
        print(f"Lain Sync: Error - {e}")


def send_stats_to_server():
    """Send current study stats to the Discord bot server"""
    config = load_config()

    server_url = config.get('server_url', '').strip().rstrip('/')
    token = config.get('token', '').strip()
    tracked_decks = config.get('tracked_decks', [])

    if not server_url or not token:
        return

    try:
        # Get stats for tracked decks
        stats = get_deck_stats(tracked_decks)

        # Build payload
        payload = {
            'tracked_decks': tracked_decks,
            'reminder_time': config.get('reminder_time') if config.get('reminder_enabled') else None,
            'timezone_offset': get_timezone_offset(),
            'due_today': stats['due'],
            'reviewed_today': stats['reviewed'],
            'new_today': stats['new'],
            'time_today': stats['time_today'],  # seconds studied today
            'time_total': stats['time_total'],  # seconds studied all-time
            'completed': stats['due'] == 0 and len(tracked_decks) > 0,
        }

        url = f"{server_url}/anki/stats?token={token}"
        data = json.dumps(payload).encode('utf-8')

        req = Request(url, data=data)
        req.add_header('Content-Type', 'application/json')
        req.add_header('User-Agent', 'LainAnkiSync/2.0')

        with urlopen(req, timeout=10) as response:
            pass

        print(f"Lain Sync: Stats sent - Due: {stats['due']}, Reviewed: {stats['reviewed']}")

    except Exception as e:
        print(f"Lain Sync: Error sending stats - {e}")


def send_config_to_server():
    """Send config to server (tracked decks, reminder time)"""
    config = load_config()

    server_url = config.get('server_url', '').strip().rstrip('/')
    token = config.get('token', '').strip()

    if not server_url or not token:
        return

    try:
        payload = {
            'tracked_decks': config.get('tracked_decks', []),
            'reminder_time': config.get('reminder_time') if config.get('reminder_enabled') else None,
            'timezone_offset': get_timezone_offset(),
        }

        url = f"{server_url}/anki/config?token={token}"
        data = json.dumps(payload).encode('utf-8')

        req = Request(url, data=data)
        req.add_header('Content-Type', 'application/json')
        req.add_header('User-Agent', 'LainAnkiSync/2.0')

        with urlopen(req, timeout=10) as response:
            pass

        print("Lain Sync: Config sent to server")

    except Exception as e:
        print(f"Lain Sync: Error sending config - {e}")


def sync_now():
    """Manual sync trigger"""
    fetch_and_sync_cards()
    send_stats_to_server()
    showInfo("Sync complete! Check the Lain Vocab deck for new cards.")


def restart_sync_timer():
    """Restart the sync timer with current settings"""
    global sync_timer

    if sync_timer:
        sync_timer.stop()

    config = load_config()
    interval = config.get('sync_interval', 30) * 1000  # Convert to milliseconds

    sync_timer = QTimer()
    sync_timer.timeout.connect(fetch_and_sync_cards)
    sync_timer.start(interval)

    print(f"Lain Sync: Card sync timer started, every {interval // 1000} seconds")


def restart_stats_timer():
    """Restart the stats reporting timer"""
    global stats_timer

    if stats_timer:
        stats_timer.stop()

    config = load_config()
    interval = config.get('stats_interval', 300) * 1000  # 5 minutes default

    stats_timer = QTimer()
    stats_timer.timeout.connect(send_stats_to_server)
    stats_timer.start(interval)

    print(f"Lain Sync: Stats timer started, reporting every {interval // 1000} seconds")


def open_config_dialog():
    """Open the configuration dialog"""
    dialog = LainSyncConfig(mw)
    dialog.exec()


def setup_menu():
    """Add menu items to Anki"""
    # Add to Tools menu
    config_action = QAction("Lain Sync Settings...", mw)
    config_action.triggered.connect(open_config_dialog)
    mw.form.menuTools.addAction(config_action)

    sync_action = QAction("Lain Sync Now", mw)
    sync_action.triggered.connect(sync_now)
    mw.form.menuTools.addAction(sync_action)


def on_init():
    """Initialize timers after Anki is fully loaded"""
    restart_sync_timer()
    restart_stats_timer()
    # Send initial stats
    send_stats_to_server()


# Initialize when Anki loads
gui_hooks.main_window_did_init.append(setup_menu)
gui_hooks.main_window_did_init.append(on_init)
