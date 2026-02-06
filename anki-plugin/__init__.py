# Lain Discord Bot - Anki Sync Plugin
# Syncs vocabulary cards from Discord to Anki

import json
import os
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

from aqt import mw, gui_hooks
from aqt.qt import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QMessageBox, QTimer
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
    'enabled': True
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


class LainSyncConfig(QDialog):
    """Configuration dialog for Lain Sync"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = load_config()
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("Lain Sync Settings")
        self.setMinimumWidth(400)

        layout = QVBoxLayout()

        # Server URL
        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("Server URL:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("http://your-server:8765")
        self.url_input.setText(self.config.get('server_url', ''))
        url_layout.addWidget(self.url_input)
        layout.addLayout(url_layout)

        # Token
        token_layout = QHBoxLayout()
        token_layout.addWidget(QLabel("Your Token:"))
        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText("Paste your token from /anki_setup")
        self.token_input.setText(self.config.get('token', ''))
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        token_layout.addWidget(self.token_input)
        layout.addLayout(token_layout)

        # Show/hide token button
        self.show_token_btn = QPushButton("Show Token")
        self.show_token_btn.clicked.connect(self.toggle_token_visibility)
        layout.addWidget(self.show_token_btn)

        # Deck name
        deck_layout = QHBoxLayout()
        deck_layout.addWidget(QLabel("Deck Name:"))
        self.deck_input = QLineEdit()
        self.deck_input.setText(self.config.get('deck_name', 'Lain Vocab'))
        deck_layout.addWidget(self.deck_input)
        layout.addLayout(deck_layout)

        # Sync interval
        interval_layout = QHBoxLayout()
        interval_layout.addWidget(QLabel("Sync Interval (seconds):"))
        self.interval_input = QSpinBox()
        self.interval_input.setRange(10, 300)
        self.interval_input.setValue(self.config.get('sync_interval', 30))
        interval_layout.addWidget(self.interval_input)
        layout.addLayout(interval_layout)

        # Test connection button
        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self.test_connection)
        layout.addWidget(test_btn)

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
            req.add_header('User-Agent', 'LainAnkiSync/1.0')

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
        save_config(self.config)

        # Restart the sync timer with new settings
        restart_sync_timer()

        showInfo("Settings saved! Sync will use the new settings.")
        self.accept()


# Global sync timer
sync_timer: Optional[QTimer] = None


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
        req.add_header('User-Agent', 'LainAnkiSync/1.0')

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
            confirm_req.add_header('User-Agent', 'LainAnkiSync/1.0')

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


def sync_now():
    """Manual sync trigger"""
    fetch_and_sync_cards()
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

    print(f"Lain Sync: Timer started, syncing every {interval // 1000} seconds")


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


# Initialize when Anki loads
gui_hooks.main_window_did_init.append(setup_menu)
gui_hooks.main_window_did_init.append(restart_sync_timer)
