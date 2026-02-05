import os
import re
import random
import asyncio
from datetime import datetime, time
from zoneinfo import ZoneInfo
import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")


# ============ KRADFILE Loading ============

def load_kradfile():
    """Load KRADFILE and return a dict mapping kanji to their radicals"""
    krad_map = {}
    kradfile_path = Path(__file__).parent / "kradfile-u"

    if not kradfile_path.exists():
        print("Warning: kradfile-u not found")
        return krad_map

    with open(kradfile_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if ' : ' in line:
                parts = line.split(' : ')
                if len(parts) == 2:
                    kanji = parts[0].strip()
                    radicals = set(parts[1].split())
                    krad_map[kanji] = radicals

    return krad_map


# Load KRADFILE on startup
KRAD_MAP = load_kradfile()


# ============ Kana Utilities ============

def normalize_kana(text):
    """Convert katakana to hiragana for comparison purposes"""
    if not text:
        return text
    result = []
    for char in text:
        code = ord(char)
        if 0x30A0 <= code <= 0x30FF:
            result.append(chr(code - 0x60))
        else:
            result.append(char)
    return ''.join(result)


def normalize_small_kana(char):
    """Convert small kana to their full-size equivalents"""
    small_to_full = {
        'ゃ': 'や', 'ゅ': 'ゆ', 'ょ': 'よ',
        'ぁ': 'あ', 'ぃ': 'い', 'ぅ': 'う', 'ぇ': 'え', 'ぉ': 'お',
        'ゎ': 'わ', 'っ': 'つ',
        'ャ': 'ヤ', 'ュ': 'ユ', 'ョ': 'ヨ',
        'ァ': 'ア', 'ィ': 'イ', 'ゥ': 'ウ', 'ェ': 'エ', 'ォ': 'オ',
        'ヮ': 'ワ', 'ッ': 'ツ', 'ヵ': 'カ', 'ヶ': 'ケ'
    }
    return small_to_full.get(char, char)


def get_first_kana(text):
    """Extract the first kana from text (raw, no normalization)"""
    if not text:
        return ""
    for char in text:
        code = ord(char)
        if (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF):
            return char
    return ""


def get_last_kana(text):
    """Extract the last kana from text, skipping ー (raw, no normalization)"""
    if not text:
        return ""
    for i in range(len(text) - 1, -1, -1):
        char = text[i]
        code = ord(char)
        if ((0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF)) and char != 'ー':
            return char
    return ""


def normalize_for_comparison(kana):
    """Normalize kana for comparison: katakana->hiragana, small->full"""
    if not kana:
        return ""
    # First normalize katakana to hiragana
    normalized = normalize_kana(kana)
    # Then normalize small kana to full-size
    return normalize_small_kana(normalized)


def is_kana_only(text):
    """Check if text contains only kana characters"""
    for char in text:
        code = ord(char)
        if not ((0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF)):
            return False
    return True


def contains_kanji(text):
    """Check if text contains kanji"""
    for char in text:
        code = ord(char)
        if 0x4E00 <= code <= 0x9FFF:
            return True
    return False


# ============ Shiritori Game ============

COMMON_KANA = ['あ', 'い', 'う', 'え', 'お', 'か', 'き', 'く', 'け', 'こ',
               'さ', 'し', 'す', 'せ', 'そ', 'た', 'ち', 'つ', 'て', 'と',
               'な', 'に', 'ぬ', 'ね', 'の', 'は', 'ひ', 'ふ', 'へ', 'ほ',
               'ま', 'み', 'む', 'め', 'も', 'や', 'ゆ', 'よ',
               'ら', 'り', 'る', 'れ', 'ろ', 'わ']


# ============ Immersion Mode ============
# Tracks channels with immersion mode: {channel_id: "jp" or "en"}
immersion_channels = {}

# Maximum allowed meaningful English words in Japanese immersion mode
MAX_ENGLISH_WORDS_JP_MODE = 2
# Maximum allowed meaningful Japanese "words" (consecutive JP char sequences) in English mode
MAX_JAPANESE_CHUNKS_EN_MODE = 2


def is_japanese_char(char):
    """Check if a character is Japanese (hiragana, katakana, kanji)"""
    code = ord(char)
    return (
        (0x3040 <= code <= 0x309F) or  # Hiragana
        (0x30A0 <= code <= 0x30FF) or  # Katakana
        (0x4E00 <= code <= 0x9FFF) or  # CJK Unified Ideographs (Kanji)
        (0xFF65 <= code <= 0xFF9F)     # Half-width Katakana
    )


def is_english_char(char):
    """Check if a character is English (ASCII letters)"""
    return char.isascii() and char.isalpha()


# Words/patterns to ignore when counting English in Japanese immersion
IGNORED_ENGLISH_PATTERNS = {
    # Japanese internet slang (wwww = laughing)
    'w', 'ww', 'www', 'wwww', 'wwwww', 'wwwwww', 'wwwwwww', 'wwwwwwww',
    # Common internet expressions used in Japanese
    'lol', 'lmao', 'lmfao', 'rofl', 'xd', 'omg', 'wtf', 'btw', 'gg', 'wp',
    # Common English used in Japanese (borrowed words often typed in romaji)
    'ok', 'ng', 'vs', 'pc', 'tv', 'cd', 'dvd', 'sns', 'dm', 'id', 'rip',
    # Roman numerals
    'i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x',
    'xi', 'xii', 'xiii', 'xiv', 'xv', 'xvi', 'xvii', 'xviii', 'xix', 'xx',
    # Emoticon components and common sounds
    'd', 'p', 'o', 'xd', 'orz',
}


def extract_english_words(text):
    """Extract sequences of ASCII letters from text"""
    return re.findall(r'[a-zA-Z]+', text)


def count_meaningful_english_words(text):
    """
    Count English words that aren't common Japanese internet terms,
    roman numerals, or single characters.
    """
    words = extract_english_words(text)
    meaningful_count = 0

    for word in words:
        lower_word = word.lower()

        # Skip ignored patterns
        if lower_word in IGNORED_ENGLISH_PATTERNS:
            continue

        # Skip if it's all 'w' (any length of wwww)
        if set(lower_word) == {'w'}:
            continue

        # Skip single characters
        if len(word) == 1:
            continue

        meaningful_count += 1

    return meaningful_count


def count_japanese_chars(text):
    """Count total Japanese characters in text"""
    return sum(1 for char in text if is_japanese_char(char))


def count_japanese_chunks(text):
    """Count separate Japanese text segments (consecutive JP characters)"""
    # Match sequences of Japanese characters
    jp_pattern = r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF65-\uFF9F]+'
    chunks = re.findall(jp_pattern, text)
    return len(chunks)


def check_immersion_compliance(text, mode):
    """
    Check if a message complies with immersion mode rules.
    Returns (compliant, reason)

    For JP mode: Message should be primarily Japanese.
                 Allow up to 2 meaningful English words if there's Japanese content.
    For EN mode: Message should be primarily English.
                 Allow up to 2 Japanese word chunks if there's English content.
    """
    meaningful_en_words = count_meaningful_english_words(text)
    jp_char_count = count_japanese_chars(text)
    jp_chunks = count_japanese_chunks(text)

    if mode == "jp":
        # Japanese immersion mode

        # If there's substantial Japanese content (5+ chars), allow some English
        if jp_char_count >= 5:
            if meaningful_en_words <= MAX_ENGLISH_WORDS_JP_MODE:
                return True, None
            else:
                return False, f"Too many English words ({meaningful_en_words}) / 英語の単語が多すぎます ({meaningful_en_words}個)"

        # If little/no Japanese and has meaningful English words, not compliant
        if jp_char_count < 5 and meaningful_en_words > 0:
            return False, "Not enough Japanese content / 日本語が足りません"

        # Empty or just symbols/numbers - allow
        return True, None

    elif mode == "en":
        # English immersion mode
        en_words = extract_english_words(text)
        meaningful_en = count_meaningful_english_words(text)

        # If there's substantial English content (3+ words), allow some Japanese
        if len(en_words) >= 3 or meaningful_en >= 2:
            if jp_chunks <= MAX_JAPANESE_CHUNKS_EN_MODE:
                return True, None
            else:
                return False, f"Too much Japanese ({jp_chunks} segments) / 日本語が多すぎます"

        # If heavy Japanese with little English, not compliant
        if jp_char_count >= 5 and meaningful_en == 0:
            return False, "Not enough English content / 英語が足りません"

        # Empty or just symbols/numbers - allow
        return True, None

    return True, None


def calculate_language_ratio(text):
    """
    Calculate the ratio of Japanese and English characters in text.
    Returns (jp_ratio, en_ratio) - ratios of Japanese and English chars
    Used for translation language detection.
    """
    jp_count = 0
    en_count = 0
    total_lang_chars = 0

    for char in text:
        if is_japanese_char(char):
            jp_count += 1
            total_lang_chars += 1
        elif is_english_char(char):
            en_count += 1
            total_lang_chars += 1
        # Ignore spaces, numbers, punctuation, etc.

    if total_lang_chars == 0:
        return 0.0, 0.0

    return jp_count / total_lang_chars, en_count / total_lang_chars


class GameMode:
    VS_BOT = "vs_bot"           # /shiritori1 - play against the bot
    MULTIPLAYER = "multiplayer"  # /shiritori2 - multiplayer with scoring
    WORD_BASKET = "word_basket"  # /wordbasket - match start AND end kana


class ShiritoriGame:
    def __init__(self, channel_id, mode):
        self.channel_id = channel_id
        self.mode = mode
        self.used_words = set()
        self.current_kana = random.choice(COMMON_KANA)
        self.end_kana = None  # For word basket mode
        self.chain_count = 0
        self.last_word = None
        self.last_reading = None
        self.last_player = None
        self.scores = {}  # player_id: score (for multiplayer)

        if mode == GameMode.WORD_BASKET:
            self.end_kana = self._get_different_kana(self.current_kana)

    def _get_different_kana(self, exclude):
        """Get a random kana different from the excluded one"""
        choices = [k for k in COMMON_KANA if k != exclude]
        return random.choice(choices)

    def add_score(self, player_id):
        """Add a point to a player's score"""
        self.scores[player_id] = self.scores.get(player_id, 0) + 1

    def get_scores_display(self):
        """Get formatted scores string"""
        if not self.scores:
            return "No scores yet"
        sorted_scores = sorted(self.scores.items(), key=lambda x: x[1], reverse=True)
        return "\n".join([f"<@{pid}>: {score} pts" for pid, score in sorted_scores])


active_games = {}


def is_noun(senses):
    """Check if any sense indicates this is a noun"""
    noun_types = [
        'Noun', 'Noun - used as a suffix', 'Noun - used as a prefix',
        'Noun, used as a suffix', 'Noun, used as a prefix',
        'Proper noun', 'Pronoun', 'Adverbial noun', 'Temporal noun',
        'Noun or verb acting prenominally', 'Noun which may take the genitive case particle \'no\'',
        'Suru verb - included', 'Noun, Adverbial'
    ]
    for sense in senses:
        parts = sense.get('parts_of_speech', [])
        for part in parts:
            for noun_type in noun_types:
                if noun_type.lower() in part.lower():
                    return True
    return False


async def lookup_word(word, required_start_kana=None):
    """
    Look up a word using Jisho API.
    Returns (is_valid, reading, meaning) or (False, None, None)

    If required_start_kana is provided, finds a reading that starts with that kana.
    Only accepts nouns.
    """
    url = f"https://jisho.org/api/v1/search/words?keyword={word}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return False, None, None

                data = await response.json()

                if not data.get('data'):
                    return False, None, None

                # Normalize required kana for comparison
                normalized_required = normalize_for_comparison(required_start_kana) if required_start_kana else None

                for entry in data['data']:
                    japanese = entry.get('japanese', [])
                    senses = entry.get('senses', [])

                    if not japanese:
                        continue

                    # Check if this entry is a noun
                    if not is_noun(senses):
                        continue

                    # Get meaning from first sense
                    meaning = ""
                    if senses and senses[0].get('english_definitions'):
                        meaning = ', '.join(senses[0]['english_definitions'][:3])

                    for jp in japanese:
                        entry_word = jp.get('word', '')
                        entry_reading = jp.get('reading', '')

                        # Check if this entry matches our input word
                        if entry_word == word or entry_reading == word:
                            reading = entry_reading if entry_reading else word

                            # If we need to match a specific starting kana
                            if normalized_required:
                                first = normalize_for_comparison(get_first_kana(reading))
                                if first != normalized_required:
                                    # This reading doesn't match, but there might be others
                                    continue

                            return True, reading, meaning

                return False, None, None
    except Exception:
        return False, None, None


async def find_bot_word(start_kana, used_words):
    """Find a word for the bot to play, starting with the given kana (nouns only)"""
    # Normalize the start kana for searching (use full-size version for better Jisho results)
    search_kana = normalize_for_comparison(start_kana)
    url = f"https://jisho.org/api/v1/search/words?keyword={search_kana}*"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return None, None, None

                data = await response.json()

                if not data.get('data'):
                    return None, None, None

                candidates = []
                normalized_start = normalize_for_comparison(start_kana)

                for entry in data['data']:
                    japanese = entry.get('japanese', [])
                    senses = entry.get('senses', [])

                    if not japanese:
                        continue

                    # Only accept nouns
                    if not is_noun(senses):
                        continue

                    for jp in japanese:
                        word = jp.get('word', '')
                        reading = jp.get('reading', '')

                        if not reading:
                            continue

                        first = normalize_for_comparison(get_first_kana(reading))
                        if first != normalized_start:
                            continue

                        last_kana = get_last_kana(reading)
                        normalized_last = normalize_for_comparison(last_kana)
                        if normalized_last == "ん":
                            continue

                        if word in used_words or reading in used_words:
                            continue

                        meaning = ""
                        if senses and senses[0].get('english_definitions'):
                            meaning = ', '.join(senses[0]['english_definitions'][:3])

                        display_word = word if word else reading
                        candidates.append((display_word, reading, meaning))

                if candidates:
                    chosen = random.choice(candidates[:10])
                    return chosen

                return None, None, None
    except Exception:
        return None, None, None

# Role IDs
COLLECTIVE_ROLE_ID = 1468122871535636551
DIARY_ROLE_ID = 1467457169770156215

# Channel IDs
DIARY_CHANNEL_ID = 1462768338563366975
WELCOME_CHANNEL_ID = 1462769962480308363

# Timezone
JAPAN_TZ = ZoneInfo("Asia/Tokyo")

ENGLISH_ROLES = {
    "beginner": 1462812945812689089,
    "intermediate": 1462812947616104541,
    "fluent": 1462812948769673444,
    "native": 1462812950183153856,
}

JAPANESE_ROLES = {
    "beginner": 1462812952389222410,
    "intermediate": 1462812953786056734,
    "fluent": 1462812954993889324,
    "native": 1462812956629667893,
}


class EnglishLevelSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Beginner / 初心者", value="beginner", emoji="🔰"),
            discord.SelectOption(label="Intermediate / 中級者", value="intermediate", emoji="📘"),
            discord.SelectOption(label="Fluent / 上級者", value="fluent", emoji="📗"),
            discord.SelectOption(label="Native / ネイティブ", value="native", emoji="🗽"),
        ]
        super().__init__(
            placeholder="Select your English level / 英語のレベルを選択",
            options=options,
            custom_id="english_level_select",
        )

    async def callback(self, interaction: discord.Interaction):
        await assign_language_role(interaction, self.values[0], ENGLISH_ROLES)


class JapaneseLevelSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Beginner / 初心者", value="beginner", emoji="🔰"),
            discord.SelectOption(label="Intermediate / 中級者", value="intermediate", emoji="📘"),
            discord.SelectOption(label="Fluent / 上級者", value="fluent", emoji="📗"),
            discord.SelectOption(label="Native / ネイティブ", value="native", emoji="🗾"),
        ]
        super().__init__(
            placeholder="Select your Japanese level / 日本語のレベルを選択",
            options=options,
            custom_id="japanese_level_select",
        )

    async def callback(self, interaction: discord.Interaction):
        await assign_language_role(interaction, self.values[0], JAPANESE_ROLES)


async def assign_language_role(interaction: discord.Interaction, level: str, role_dict: dict):
    guild = interaction.guild
    member = interaction.user

    role_id = role_dict[level]
    role = guild.get_role(role_id)

    if not role:
        await interaction.response.send_message(
            "Role not found. Please contact an administrator.",
            ephemeral=True,
        )
        return

    roles_to_remove = [
        guild.get_role(rid)
        for rid in role_dict.values()
        if guild.get_role(rid) in member.roles
    ]

    if roles_to_remove:
        await member.remove_roles(*roles_to_remove)

    await member.add_roles(role)
    await interaction.response.send_message(
        f"Your role has been set to: **{role.name}**\nあなたのロールが設定されました。",
        ephemeral=True,
    )


class DiaryRoleSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="Join Daily Diary / 日記に参加",
                value="join",
                emoji="📔",
                description="Get reminders for daily diary"
            ),
            discord.SelectOption(
                label="Leave Daily Diary / 日記から退出",
                value="leave",
                emoji="❌",
                description="Stop receiving reminders"
            ),
        ]
        super().__init__(
            placeholder="Daily Diary / 日記参加",
            options=options,
            custom_id="diary_role_select",
        )

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        role = guild.get_role(DIARY_ROLE_ID)

        if not role:
            await interaction.response.send_message(
                "Role not found. Please contact an administrator.",
                ephemeral=True,
            )
            return

        if self.values[0] == "join":
            await member.add_roles(role)
            await interaction.response.send_message(
                "You've joined the Daily Diary! You'll receive reminders.\n"
                "日記に参加しました！リマインダーが届きます。",
                ephemeral=True,
            )
        else:
            await member.remove_roles(role)
            await interaction.response.send_message(
                "You've left the Daily Diary.\n"
                "日記から退出しました。",
                ephemeral=True,
            )


class RoleAssignView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(EnglishLevelSelect())
        self.add_item(JapaneseLevelSelect())
        self.add_item(DiaryRoleSelect())


class JapaneseLearningBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True  # Required for on_member_join
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.add_view(RoleAssignView())
        # Sync commands globally (can take up to 1 hour to propagate)
        try:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} global commands")
        except Exception as e:
            print(f"Failed to sync commands: {e}")


bot = JapaneseLearningBot()


# Daily diary task - runs at 10 PM Japan time
@tasks.loop(time=time(hour=22, minute=0, tzinfo=JAPAN_TZ))
async def daily_diary_task():
    """Create a daily diary post at 10 PM Japan time"""
    channel = bot.get_channel(DIARY_CHANNEL_ID)
    if not channel:
        print(f"Diary channel {DIARY_CHANNEL_ID} not found")
        return

    # Get today's date in Japan timezone
    japan_now = datetime.now(JAPAN_TZ)
    date_str = japan_now.strftime("%Y年%m月%d日")
    date_str_en = japan_now.strftime("%B %d, %Y")

    # Thread/post name
    thread_name = f"📔 {date_str} / {date_str_en}"

    # The diary prompt message
    message = (
        f"<@&{DIARY_ROLE_ID}>\n\n"
        "**Time for today's diary!**\n"
        "Talk about your day, what you learned, or anything interesting that may have happened today. "
        "This diary is for language learning, so try to use any words, grammar functions, etc. that you may have learned.\n\n"
        "**今日の日記の時間です！**\n"
        "今日あったこと、学んだこと、面白かったことなどを書いてみましょう。"
        "この日記は語学学習のためのものなので、学んだ単語や文法などを使ってみてください。"
    )

    try:
        # Check if it's a forum channel
        if isinstance(channel, discord.ForumChannel):
            # For forum channels, create a post (thread with initial message)
            thread, initial_message = await channel.create_thread(
                name=thread_name,
                content=message,
            )
            print(f"Created diary forum post: {thread_name}")
        else:
            # For regular text channels, create a thread
            thread = await channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.public_thread,
            )
            await thread.send(message)
            print(f"Created diary thread: {thread_name}")

    except Exception as e:
        print(f"Failed to create diary thread/post: {e}")


@daily_diary_task.before_loop
async def before_daily_diary():
    """Wait until the bot is ready before starting the task"""
    await bot.wait_until_ready()


# Daily Waaduru reset task - runs at midnight Japan time
@tasks.loop(time=time(hour=0, minute=0, tzinfo=JAPAN_TZ))
async def daily_waaduru_reset_task():
    """Reset daily waaduru games at midnight Japan time"""
    global daily_waaduru_word, active_daily_waaduru_games

    # Clear all active daily games
    active_daily_waaduru_games.clear()

    # Clear the cached daily word so a new one will be generated
    daily_waaduru_word = {"date": None, "word": None}

    print(f"Daily Waaduru reset at {datetime.now(JAPAN_TZ).strftime('%Y-%m-%d %H:%M')} JST")


@daily_waaduru_reset_task.before_loop
async def before_daily_waaduru_reset():
    """Wait until the bot is ready before starting the task"""
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"Commands registered: {len(bot.tree.get_commands())}")

    # Start the daily diary task
    if not daily_diary_task.is_running():
        daily_diary_task.start()
        print("Daily diary task started")

    # Start the daily waaduru reset task
    if not daily_waaduru_reset_task.is_running():
        daily_waaduru_reset_task.start()
        print("Daily Waaduru reset task started")

    print("------")


@bot.event
async def on_member_join(member):
    """Assign the 'collective' role to new members and send welcome message"""
    guild = member.guild

    # Assign collective role
    role = guild.get_role(COLLECTIVE_ROLE_ID)
    if role:
        try:
            await member.add_roles(role)
            print(f"Assigned collective role to {member.name}")
        except discord.errors.Forbidden:
            print(f"Failed to assign role to {member.name} - missing permissions")

    # Send welcome message
    welcome_channel = bot.get_channel(WELCOME_CHANNEL_ID)
    if welcome_channel:
        try:
            await welcome_channel.send(f"**{member.display_name}** has joined the wired...")
        except discord.errors.Forbidden:
            print(f"Failed to send welcome message for {member.name}")


@bot.tree.command(name="ping", description="Check if the bot is responsive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"Pong! Latency: {round(bot.latency * 1000)}ms"
    )


@bot.tree.command(name="help", description="Show all bot commands / コマンド一覧を表示")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Japanese Learning Bot - Commands / コマンド一覧",
        color=discord.Color.blue()
    )

    # Games section
    games = (
        "**/shiritori1** - Play shiritori vs the bot\n"
        "**/shiritori2** - Multiplayer shiritori with scoring\n"
        "**/wordbasket** - Match both start AND end kana\n"
        "**/endgame** - End the current shiritori game\n"
        "**/waaduru** `random` - Guess a 2-kanji word (Wordle-style)\n"
        "**/waaduru** `daily` - Daily challenge (same word for everyone)\n"
        "**/endwaaduru** - End the current waaduru game\n"
        "**/kanjipuzzle** - Guess a word from its radicals\n"
        "**/endkanjipuzzle** - End the current kanji puzzle"
    )
    embed.add_field(name="Games / ゲーム", value=games, inline=False)

    # Lookup section
    lookup = (
        "**/jisho** `<word>` - Look up a word in the dictionary\n"
        "**/kanji** `<kanji>` - Get kanji info + stroke order\n"
        "**/pitch** `<word>` - Get pitch accent + audio\n"
        "**/translate** `<text>` - Translate JP↔EN\n"
        "**/translate** `last` - Translate the previous message"
    )
    embed.add_field(name="Lookup / 検索", value=lookup, inline=False)

    # Immersion section
    immersion = (
        "**/immersion jp** - Require Japanese in channel\n"
        "**/immersion en** - Require English in channel\n"
        "**/immersion disable** - Turn off immersion mode\n"
        "**/immersion status** - Check current mode"
    )
    embed.add_field(name="Immersion / 没入モード", value=immersion, inline=False)

    # Admin section
    admin = (
        "**/roleassign** - Create language level role panel\n"
        "**/sync** - Force sync commands to this server"
    )
    embed.add_field(name="Admin / 管理者", value=admin, inline=False)

    embed.set_footer(text="Type a command to get started!")

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="sync", description="Sync bot commands to this server (Admin only)")
@app_commands.default_permissions(administrator=True)
async def sync_commands(interaction: discord.Interaction):
    """Force sync commands to the current guild for instant availability"""
    await interaction.response.defer(ephemeral=True)
    try:
        # Copy global commands to this guild and sync
        bot.tree.copy_global_to(guild=interaction.guild)
        synced = await bot.tree.sync(guild=interaction.guild)
        await interaction.followup.send(
            f"✅ Synced {len(synced)} commands to this server!\n"
            f"Commands should now be available immediately.",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Sync failed: {e}", ephemeral=True)


@bot.tree.command(name="roleassign", description="Create the role assignment panel (Admin only)")
@app_commands.default_permissions(administrator=True)
async def roleassign(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    embed = discord.Embed(
        title="Language Level Selection / 言語レベルの選択",
        description=(
            "**What is your English level?**\n"
            "英語のレベルは何ですか？\n\n"
            "🔰 Beginner / 初心者\n"
            "📘 Intermediate / 中級者\n"
            "📗 Fluent / 上級者\n"
            "🗽 Native / ネイティブ\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "**What is your Japanese level?**\n"
            "日本語のレベルは何ですか？\n\n"
            "🔰 Beginner / 初心者\n"
            "📘 Intermediate / 中級者\n"
            "📗 Fluent / 上級者\n"
            "🗾 Native / ネイティブ\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "**📔 Daily Diary / 日記**\n"
            "If you would like to participate and be reminded of the daily diary, select this role.\n"
            "日記に参加してリマインダーを受け取りたい場合は、このロールを選択してください。"
        ),
        color=discord.Color.blurple(),
    )

    await interaction.channel.send(embed=embed, view=RoleAssignView())
    await interaction.followup.send("Role panel created!", ephemeral=True)


@bot.tree.command(name="shiritori1", description="Play Shiritori against the bot / ボットとしりとり対戦")
async def shiritori1(interaction: discord.Interaction):
    channel_id = interaction.channel_id

    if channel_id in active_games:
        await interaction.response.send_message(
            "A game is already running in this channel! Use `/endgame` to end it.\n"
            "このチャンネルではすでにゲームが進行中です！`/endgame`で終了できます。",
            ephemeral=True
        )
        return

    game = ShiritoriGame(channel_id, GameMode.VS_BOT)
    active_games[channel_id] = game

    embed = discord.Embed(
        title="🤖 Shiritori vs Bot / ボットとしりとり",
        description=(
            "**Game started! / ゲーム開始！**\n\n"
            f"First word must start with: **{game.current_kana}**\n"
            f"最初の言葉は「**{game.current_kana}**」で始めてください\n\n"
            "**Rules / ルール:**\n"
            "• You play against the bot! / ボットと対戦！\n"
            "• Type a word, then the bot responds\n"
            "• 言葉を入力すると、ボットが返答します\n"
            "• Words ending in ん lose! / 「ん」で終わる言葉は負け！\n\n"
            "Use `/endgame` to end the game\n"
            "`/endgame`でゲーム終了"
        ),
        color=discord.Color.green()
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="shiritori2", description="Multiplayer Shiritori with scoring / みんなでしりとり（スコア付き）")
async def shiritori2(interaction: discord.Interaction):
    channel_id = interaction.channel_id

    if channel_id in active_games:
        await interaction.response.send_message(
            "A game is already running in this channel! Use `/endgame` to end it.\n"
            "このチャンネルではすでにゲームが進行中です！`/endgame`で終了できます。",
            ephemeral=True
        )
        return

    game = ShiritoriGame(channel_id, GameMode.MULTIPLAYER)
    active_games[channel_id] = game

    embed = discord.Embed(
        title="👥 Multiplayer Shiritori / みんなでしりとり",
        description=(
            "**Game started! / ゲーム開始！**\n\n"
            f"First word must start with: **{game.current_kana}**\n"
            f"最初の言葉は「**{game.current_kana}**」で始めてください\n\n"
            "**Rules / ルール:**\n"
            "• First person to answer correctly gets 1 point!\n"
            "• 最初に正解した人が1ポイント獲得！\n"
            "• Words ending in ん lose! / 「ん」で終わる言葉は負け！\n"
            "• No repeating words / 同じ言葉は使えません\n\n"
            "Use `/endgame` to end and see scores\n"
            "`/endgame`でゲーム終了＆スコア表示"
        ),
        color=discord.Color.blue()
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="wordbasket", description="Word Basket mode - match start AND end kana / ワードバスケット")
async def wordbasket(interaction: discord.Interaction):
    channel_id = interaction.channel_id

    if channel_id in active_games:
        await interaction.response.send_message(
            "A game is already running in this channel! Use `/endgame` to end it.\n"
            "このチャンネルではすでにゲームが進行中です！`/endgame`で終了できます。",
            ephemeral=True
        )
        return

    game = ShiritoriGame(channel_id, GameMode.WORD_BASKET)
    active_games[channel_id] = game

    embed = discord.Embed(
        title="🧺 Word Basket / ワードバスケット",
        description=(
            "**Game started! / ゲーム開始！**\n\n"
            f"Word must **start** with: **{game.current_kana}**\n"
            f"Word must **end** with: **{game.end_kana}**\n\n"
            f"「**{game.current_kana}**」で始まり「**{game.end_kana}**」で終わる言葉\n\n"
            "**Rules / ルール:**\n"
            "• Word must match BOTH start and end kana!\n"
            "• 始まりと終わりの両方が一致する言葉！\n"
            "• First correct answer gets 1 point\n"
            "• 最初に正解した人が1ポイント獲得\n\n"
            "Use `/endgame` to end and see scores\n"
            "`/endgame`でゲーム終了＆スコア表示"
        ),
        color=discord.Color.purple()
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="endgame", description="End the current game / ゲームを終了する")
async def endgame(interaction: discord.Interaction):
    channel_id = interaction.channel_id

    if channel_id not in active_games:
        await interaction.response.send_message(
            "No game is running in this channel.\nこのチャンネルではゲームが進行していません。",
            ephemeral=True
        )
        return

    game = active_games.pop(channel_id)

    if game.mode == GameMode.VS_BOT:
        embed = discord.Embed(
            title="🏁 Game Over / ゲーム終了",
            description=(
                f"**Final chain: {game.chain_count} words / 最終チェーン: {game.chain_count}語**\n\n"
                f"Last word: {game.last_word or 'None'}\n"
                f"最後の言葉: {game.last_word or 'なし'}"
            ),
            color=discord.Color.red()
        )
    else:
        embed = discord.Embed(
            title="🏁 Game Over / ゲーム終了",
            description=(
                f"**Final chain: {game.chain_count} words / 最終チェーン: {game.chain_count}語**\n\n"
                f"**Scores / スコア:**\n{game.get_scores_display()}"
            ),
            color=discord.Color.red()
        )

    await interaction.response.send_message(embed=embed)


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    channel_id = message.channel.id
    content = message.content.strip()

    if not content:
        return

    # Check for Immersion Mode
    if channel_id in immersion_channels:
        mode = immersion_channels[channel_id]
        compliant, reason = check_immersion_compliance(content, mode)

        if not compliant:
            try:
                await message.delete()
                lang_name = "Japanese / 日本語" if mode == "jp" else "English / 英語"
                await message.channel.send(
                    f"⚠️ {message.author.mention} - This channel is in {lang_name} immersion mode.\n"
                    f"Reason: {reason}",
                    delete_after=5
                )
            except discord.errors.Forbidden:
                pass  # Bot doesn't have permission to delete
            return

    # Check for Daily Waaduru game first (user-specific)
    user_id = message.author.id
    if user_id in active_daily_waaduru_games:
        game = active_daily_waaduru_games[user_id]
        # Only process if message is in the same channel and game is active
        if game.channel_id == channel_id and not game.is_game_over():
            if len(content) == 2:
                has_kanji = all(char in KRAD_MAP for char in content)
                if has_kanji:
                    await handle_daily_waaduru_guess(message, content)
                    return

    # Check for regular Waaduru game (channel-specific)
    if channel_id in active_waaduru_games:
        # Only process 2-character messages that look like kanji
        if len(content) == 2:
            has_kanji = all(char in KRAD_MAP for char in content)
            if has_kanji:
                await handle_waaduru_guess(message, content)
                return

    # Check for Kanji Puzzle game
    if channel_id in active_kanjipuzzle_games:
        # Only process 2-character messages that look like kanji
        if len(content) == 2:
            has_kanji = all(char in KRAD_MAP for char in content)
            if has_kanji:
                await handle_kanjipuzzle_guess(message, content)
                return

    # Check for Shiritori game
    if channel_id not in active_games:
        return

    has_jp = False
    for char in content:
        code = ord(char)
        if (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF) or (0x4E00 <= code <= 0x9FFF):
            has_jp = True
            break

    if not has_jp:
        return

    game = active_games[channel_id]

    # Look up word with required starting kana (handles multiple readings for kanji)
    is_valid, reading, meaning = await lookup_word(content, game.current_kana)

    if not is_valid:
        await message.add_reaction("❓")
        await message.reply(
            f"Word not found (must be a noun starting with **{game.current_kana}**)\n"
            f"見つかりません（「**{game.current_kana}**」で始まる名詞のみ有効）: **{content}**",
            delete_after=5
        )
        return

    last_kana = get_last_kana(reading)
    normalized_last = normalize_for_comparison(last_kana)

    # Word Basket mode: also check end kana
    if game.mode == GameMode.WORD_BASKET:
        required_end = normalize_for_comparison(game.end_kana)
        if normalized_last != required_end:
            await message.add_reaction("❌")
            await message.reply(
                f"Word must end with **{game.end_kana}** / 「**{game.end_kana}**」で終わる言葉を入力してください\n"
                f"Your word ends with: {last_kana}",
                delete_after=5
            )
            return

    if content in game.used_words or reading in game.used_words:
        await message.add_reaction("🔄")
        await message.reply(
            f"Word already used! / この言葉はすでに使われています！",
            delete_after=5
        )
        return

    # Check for ん ending (not applicable in word basket since end kana is controlled)
    if game.mode != GameMode.WORD_BASKET and normalize_for_comparison(last_kana) == "ん":
        game_over = active_games.pop(channel_id)
        await message.add_reaction("💀")

        desc = (
            f"**{message.author.display_name}** used a word ending in ん!\n"
            f"「ん」で終わる言葉を使いました！\n\n"
            f"Word / 言葉: **{content}** ({reading})\n"
            f"Meaning / 意味: {meaning}\n\n"
            f"**Final chain: {game_over.chain_count} words / 最終チェーン: {game_over.chain_count}語**"
        )

        if game_over.mode == GameMode.MULTIPLAYER:
            desc += f"\n\n**Scores / スコア:**\n{game_over.get_scores_display()}"

        embed = discord.Embed(
            title="💀 Game Over! / ゲームオーバー！",
            description=desc,
            color=discord.Color.red()
        )
        await message.reply(embed=embed)
        return

    # Valid word! Update game state
    game.used_words.add(content)
    game.used_words.add(reading)
    game.chain_count += 1
    game.last_word = content
    game.last_reading = reading
    game.last_player = message.author.id

    await message.add_reaction("✅")

    # Handle different game modes
    if game.mode == GameMode.VS_BOT:
        # Update current kana for bot's turn
        game.current_kana = last_kana

        embed = discord.Embed(
            title=f"✅ {content}",
            description=(
                f"**Reading / 読み:** {reading}\n"
                f"**Meaning / 意味:** {meaning}\n"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Chain: {game.chain_count}")
        await message.reply(embed=embed)

        # Bot's turn
        bot_word, bot_reading, bot_meaning = await find_bot_word(last_kana, game.used_words)

        if bot_word is None:
            active_games.pop(channel_id)
            embed = discord.Embed(
                title="🎉 You Win! / あなたの勝ち！",
                description=(
                    f"The bot couldn't find a word starting with **{last_kana}**!\n"
                    f"ボットは「**{last_kana}**」で始まる言葉が見つかりませんでした！\n\n"
                    f"**Final chain: {game.chain_count} words / 最終チェーン: {game.chain_count}語**"
                ),
                color=discord.Color.gold()
            )
            await message.channel.send(embed=embed)
            return

        bot_last_kana = get_last_kana(bot_reading)

        game.used_words.add(bot_word)
        game.used_words.add(bot_reading)
        game.chain_count += 1
        game.current_kana = bot_last_kana
        game.last_word = bot_word
        game.last_reading = bot_reading

        embed = discord.Embed(
            title=f"🤖 {bot_word}",
            description=(
                f"**Reading / 読み:** {bot_reading}\n"
                f"**Meaning / 意味:** {bot_meaning}\n\n"
                f"Your turn! Next word starts with: **{bot_last_kana}**\n"
                f"あなたの番！次は「**{bot_last_kana}**」で始まる言葉"
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Chain: {game.chain_count}")
        await message.channel.send(embed=embed)

    elif game.mode == GameMode.MULTIPLAYER:
        # Update current kana
        game.current_kana = last_kana
        # Add point to player
        game.add_score(message.author.id)

        embed = discord.Embed(
            title=f"✅ {content}",
            description=(
                f"**Reading / 読み:** {reading}\n"
                f"**Meaning / 意味:** {meaning}\n\n"
                f"**+1 point to {message.author.display_name}!**\n\n"
                f"Next word starts with: **{last_kana}**\n"
                f"次は「**{last_kana}**」で始まる言葉"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Chain: {game.chain_count} | Score: {game.scores.get(message.author.id, 0)} pts")
        await message.reply(embed=embed)

    elif game.mode == GameMode.WORD_BASKET:
        # Add point to player
        game.add_score(message.author.id)

        # Generate new start and end kana
        game.current_kana = random.choice(COMMON_KANA)
        game.end_kana = game._get_different_kana(game.current_kana)

        embed = discord.Embed(
            title=f"✅ {content}",
            description=(
                f"**Reading / 読み:** {reading}\n"
                f"**Meaning / 意味:** {meaning}\n\n"
                f"**+1 point to {message.author.display_name}!**\n\n"
                f"**Next challenge:**\n"
                f"Start with: **{game.current_kana}** | End with: **{game.end_kana}**\n"
                f"「**{game.current_kana}**」で始まり「**{game.end_kana}**」で終わる言葉"
            ),
            color=discord.Color.purple()
        )
        embed.set_footer(text=f"Chain: {game.chain_count} | Score: {game.scores.get(message.author.id, 0)} pts")
        await message.reply(embed=embed)


# ============ Waaduru (Kanji Wordle) Game ============

class GuessResult:
    GREEN = "green"    # Correct kanji, correct position
    YELLOW = "yellow"  # Correct kanji, wrong position
    ORANGE = "orange"  # Shares radical with answer kanji
    GRAY = "gray"      # No match


class WaaduruGame:
    def __init__(self, channel_id, answer_word, answer_reading, answer_meaning):
        self.channel_id = channel_id
        self.answer_word = answer_word  # The 2-kanji answer
        self.answer_reading = answer_reading
        self.answer_meaning = answer_meaning
        self.guesses = []  # List of (word, results) tuples
        self.max_guesses = 5
        self.solved = False
        # Track discovered radicals for each position (0 and 1)
        self.discovered_radicals = {0: set(), 1: set()}

    def get_answer_radicals(self):
        """Get radicals for each kanji in the answer"""
        radicals = []
        for kanji in self.answer_word:
            radicals.append(KRAD_MAP.get(kanji, set()))
        return radicals

    def check_guess(self, guess_word):
        """
        Check a guess against the answer.
        Returns a list of (GuessResult, shared_info) tuples for each kanji position.
        shared_info is a dict {answer_position: set of shared radicals} for orange results.
        """
        if len(guess_word) != 2:
            return None

        results = []
        answer_radicals = self.get_answer_radicals()

        for i, guess_kanji in enumerate(guess_word):
            answer_kanji = self.answer_word[i]

            # Green: Exact match in same position
            if guess_kanji == answer_kanji:
                results.append((GuessResult.GREEN, {}))
                continue

            # Yellow: Kanji exists in answer but different position
            if guess_kanji in self.answer_word:
                results.append((GuessResult.YELLOW, {}))
                continue

            # Orange: Shares any radical with any kanji in the answer
            guess_radicals = KRAD_MAP.get(guess_kanji, set())
            shared_by_position = {}  # {answer_pos: set of shared radicals}

            for ans_pos, ans_radicals in enumerate(answer_radicals):
                shared = guess_radicals & ans_radicals
                if shared:
                    shared_by_position[ans_pos] = shared

            if shared_by_position:
                results.append((GuessResult.ORANGE, shared_by_position))
            else:
                results.append((GuessResult.GRAY, {}))

        return results

    def add_guess(self, guess_word, results):
        """Add a guess to the history and accumulate discovered radicals"""
        self.guesses.append((guess_word, results))
        if guess_word == self.answer_word:
            self.solved = True

        # Accumulate discovered radicals from orange results
        for i, (result, shared_by_position) in enumerate(results):
            if result == GuessResult.ORANGE and shared_by_position:
                for ans_pos, radicals in shared_by_position.items():
                    self.discovered_radicals[ans_pos] |= radicals

    def get_discovered_radicals_display(self):
        """Format the discovered radicals for display"""
        rad1 = " ".join(sorted(self.discovered_radicals[0])) if self.discovered_radicals[0] else "?"
        rad2 = " ".join(sorted(self.discovered_radicals[1])) if self.discovered_radicals[1] else "?"
        return f"1: ({rad1})  2: ({rad2})"

    def is_game_over(self):
        """Check if game is over (won or out of guesses)"""
        return self.solved or len(self.guesses) >= self.max_guesses

    def get_remaining_guesses(self):
        return self.max_guesses - len(self.guesses)


# Active Waaduru games: {channel_id: WaaduruGame}
active_waaduru_games = {}

# Daily Waaduru: {user_id: DailyWaaduruGame}
active_daily_waaduru_games = {}

# Daily word cache: {"date": "YYYY-MM-DD", "word": (word, reading, meaning)}
daily_waaduru_word = {"date": None, "word": None}


class DailyWaaduruGame:
    """A daily waaduru game for a specific user"""
    def __init__(self, user_id, answer_word, answer_reading, answer_meaning, channel_id, message_id):
        self.user_id = user_id
        self.answer_word = answer_word
        self.answer_reading = answer_reading
        self.answer_meaning = answer_meaning
        self.channel_id = channel_id  # Channel where the game was started
        self.message_id = message_id  # The public progress message
        self.guesses = []
        self.max_guesses = 5
        self.solved = False
        self.discovered_radicals = {0: set(), 1: set()}

    def check_guess(self, guess_word):
        """Same logic as regular WaaduruGame"""
        if len(guess_word) != 2:
            return None

        results = []
        answer_radicals = [KRAD_MAP.get(k, set()) for k in self.answer_word]

        for i, guess_kanji in enumerate(guess_word):
            answer_kanji = self.answer_word[i]

            if guess_kanji == answer_kanji:
                results.append((GuessResult.GREEN, {}))
                continue

            if guess_kanji in self.answer_word:
                results.append((GuessResult.YELLOW, {}))
                continue

            guess_radicals = KRAD_MAP.get(guess_kanji, set())
            shared_by_position = {}

            for ans_pos, ans_radicals in enumerate(answer_radicals):
                shared = guess_radicals & ans_radicals
                if shared:
                    shared_by_position[ans_pos] = shared

            if shared_by_position:
                results.append((GuessResult.ORANGE, shared_by_position))
            else:
                results.append((GuessResult.GRAY, {}))

        return results

    def add_guess(self, guess_word, results):
        self.guesses.append((guess_word, results))
        if guess_word == self.answer_word:
            self.solved = True

        for i, (result, shared_by_position) in enumerate(results):
            if result == GuessResult.ORANGE and shared_by_position:
                for ans_pos, radicals in shared_by_position.items():
                    self.discovered_radicals[ans_pos] |= radicals

    def get_discovered_radicals_display(self):
        rad1 = " ".join(sorted(self.discovered_radicals[0])) if self.discovered_radicals[0] else "?"
        rad2 = " ".join(sorted(self.discovered_radicals[1])) if self.discovered_radicals[1] else "?"
        return f"1: ({rad1})  2: ({rad2})"

    def is_game_over(self):
        return self.solved or len(self.guesses) >= self.max_guesses

    def get_remaining_guesses(self):
        return self.max_guesses - len(self.guesses)


def get_daily_date_string():
    """Get today's date in Japan timezone as YYYY-MM-DD"""
    return datetime.now(JAPAN_TZ).strftime("%Y-%m-%d")


async def get_daily_waaduru_word():
    """Get the daily word, generating a new one if the date changed"""
    global daily_waaduru_word

    today = get_daily_date_string()

    if daily_waaduru_word["date"] == today and daily_waaduru_word["word"]:
        return daily_waaduru_word["word"]

    # Generate a new word using today's date as seed for consistency
    # Use a seeded random to ensure same word for all users on the same day
    date_seed = int(today.replace("-", ""))
    seeded_random = random.Random(date_seed)

    common_kanji = ['日', '月', '水', '火', '木', '金', '土', '人', '大', '小',
                    '山', '川', '田', '中', '出', '入', '上', '下', '生', '学',
                    '会', '社', '国', '本', '電', '車', '食', '飲', '話', '語',
                    '読', '書', '見', '聞', '言', '思', '知', '気', '手', '足',
                    '目', '耳', '口', '心', '体', '頭', '顔', '名', '前', '後',
                    '左', '右', '東', '西', '南', '北', '春', '夏', '秋', '冬',
                    '朝', '昼', '夜', '今', '先', '来', '年', '時', '分', '間',
                    '長', '短', '高', '低', '新', '古', '若', '老', '男', '女']

    # Shuffle with seed so we try kanji in a deterministic order
    shuffled_kanji = common_kanji.copy()
    seeded_random.shuffle(shuffled_kanji)

    for start_kanji in shuffled_kanji:
        url = f"https://jisho.org/api/v1/search/words?keyword={start_kanji}*"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        continue

                    data = await response.json()

                    if not data.get('data'):
                        continue

                    candidates = []

                    for entry in data['data']:
                        japanese = entry.get('japanese', [])
                        senses = entry.get('senses', [])

                        if not japanese or not is_noun(senses):
                            continue

                        for jp in japanese:
                            word = jp.get('word', '')
                            reading = jp.get('reading', '')

                            if len(word) != 2:
                                continue

                            if word[0] not in KRAD_MAP or word[1] not in KRAD_MAP:
                                continue

                            meaning = ""
                            if senses and senses[0].get('english_definitions'):
                                meaning = ', '.join(senses[0]['english_definitions'][:3])

                            candidates.append((word, reading, meaning))
                            break

                    if candidates:
                        # Use seeded random to pick consistently
                        chosen = seeded_random.choice(candidates)
                        daily_waaduru_word = {"date": today, "word": chosen}
                        return chosen

        except Exception:
            continue

    return None


def format_daily_public_result(results):
    """Format results showing only colors (no kanji) for public display"""
    color_emoji = {
        GuessResult.GREEN: "🟩",
        GuessResult.YELLOW: "🟨",
        GuessResult.ORANGE: "🟧",
        GuessResult.GRAY: "⬛",
    }
    return "".join(color_emoji[r] for r, _ in results)


def create_daily_public_embed(game, user_name):
    """Create a public embed showing only colors, not guessed words"""
    description_lines = []

    for guess_word, results in game.guesses:
        # Only show colors, not the actual words
        description_lines.append(format_daily_public_result(results))

    remaining = game.get_remaining_guesses()
    for _ in range(remaining):
        description_lines.append("⬜⬜")

    description = "\n".join(description_lines)

    if game.solved:
        title = f"🎉 Daily Waaduru - {user_name} solved it!"
        color = discord.Color.green()
        description += f"\n\n**Solved in {len(game.guesses)}/5 guesses!**"
    elif game.is_game_over():
        title = f"💀 Daily Waaduru - {user_name}"
        color = discord.Color.red()
        description += f"\n\n**Better luck tomorrow!**"
    else:
        title = f"📝 Daily Waaduru - {user_name} ({len(game.guesses)}/5)"
        color = discord.Color.blue()

    embed = discord.Embed(title=title, description=description, color=color)
    japan_date = datetime.now(JAPAN_TZ).strftime("%Y年%m月%d日")
    embed.set_footer(text=f"Daily Challenge / 今日のチャレンジ - {japan_date}")
    return embed


def create_daily_private_embed(game, guess_word, results):
    """Create a private embed showing full details for the user"""
    description_lines = []

    for gw, res in game.guesses:
        description_lines.append(format_waaduru_result(gw, res))

    remaining = game.get_remaining_guesses()
    for _ in range(remaining):
        description_lines.append("＿ ＿  ⬜⬜")

    description = "\n".join(description_lines)

    if game.guesses and not game.solved:
        description += f"\n\n**Discovered Radicals / 発見した部首:**\n{game.get_discovered_radicals_display()}"

    if game.solved:
        title = "🎉 You solved today's Daily Waaduru!"
        color = discord.Color.green()
        description += f"\n\n**Answer / 答え:** {game.answer_word} ({game.answer_reading})\n**Meaning / 意味:** {game.answer_meaning}"
    elif game.is_game_over():
        title = "💀 Daily Waaduru - Game Over"
        color = discord.Color.red()
        description += f"\n\n**Answer / 答え:** {game.answer_word} ({game.answer_reading})\n**Meaning / 意味:** {game.answer_meaning}"
    else:
        title = f"📝 Daily Waaduru ({len(game.guesses)}/{game.max_guesses})"
        color = discord.Color.blue()
        description += "\n\n🟩 Correct position / 🟨 Wrong position / 🟧 Shared radical / ⬛ No match"

    embed = discord.Embed(title=title, description=description, color=color)
    return embed


def format_waaduru_result(guess_word, results):
    """Format a guess result with colored squares"""
    color_emoji = {
        GuessResult.GREEN: "🟩",
        GuessResult.YELLOW: "🟨",
        GuessResult.ORANGE: "🟧",
        GuessResult.GRAY: "⬛",
    }

    squares = "".join(color_emoji[r] for r, _ in results)
    return f"{guess_word[0]} {guess_word[1]}  {squares}"


def create_waaduru_embed(game, show_answer=False):
    """Create an embed showing the current game state"""
    description_lines = []

    # Show all guesses
    for guess_word, results in game.guesses:
        description_lines.append(format_waaduru_result(guess_word, results))

    # Show empty slots for remaining guesses
    remaining = game.get_remaining_guesses()
    for _ in range(remaining):
        description_lines.append("＿ ＿  ⬜⬜")

    description = "\n".join(description_lines)

    # Show discovered radicals (running tally)
    if game.guesses and not game.solved:
        description += f"\n\n**Discovered Radicals / 発見した部首:**\n{game.get_discovered_radicals_display()}"

    if game.solved:
        title = "🎉 Waaduru - You Win! / 正解！"
        color = discord.Color.green()
        description += f"\n\n**Answer / 答え:** {game.answer_word} ({game.answer_reading})\n**Meaning / 意味:** {game.answer_meaning}"
    elif game.is_game_over():
        title = "💀 Waaduru - Game Over / ゲームオーバー"
        color = discord.Color.red()
        description += f"\n\n**Answer / 答え:** {game.answer_word} ({game.answer_reading})\n**Meaning / 意味:** {game.answer_meaning}"
    else:
        title = f"📝 Waaduru ({len(game.guesses)}/{game.max_guesses})"
        color = discord.Color.blue()
        description += "\n\n🟩 Correct position / 🟨 Wrong position / 🟧 Shared radical / ⬛ No match"

    embed = discord.Embed(title=title, description=description, color=color)
    return embed


async def get_random_jukugo():
    """Get a random 2-kanji compound noun from Jisho"""
    # Common starting kanji for jukugo
    common_kanji = ['日', '月', '水', '火', '木', '金', '土', '人', '大', '小',
                    '山', '川', '田', '中', '出', '入', '上', '下', '生', '学',
                    '会', '社', '国', '本', '電', '車', '食', '飲', '話', '語',
                    '読', '書', '見', '聞', '言', '思', '知', '気', '手', '足',
                    '目', '耳', '口', '心', '体', '頭', '顔', '名', '前', '後',
                    '左', '右', '東', '西', '南', '北', '春', '夏', '秋', '冬',
                    '朝', '昼', '夜', '今', '先', '来', '年', '時', '分', '間',
                    '長', '短', '高', '低', '新', '古', '若', '老', '男', '女']

    for _ in range(10):  # Try up to 10 times
        start_kanji = random.choice(common_kanji)
        url = f"https://jisho.org/api/v1/search/words?keyword={start_kanji}*"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        continue

                    data = await response.json()

                    if not data.get('data'):
                        continue

                    candidates = []

                    for entry in data['data']:
                        japanese = entry.get('japanese', [])
                        senses = entry.get('senses', [])

                        if not japanese or not is_noun(senses):
                            continue

                        for jp in japanese:
                            word = jp.get('word', '')
                            reading = jp.get('reading', '')

                            # Must be exactly 2 kanji
                            if len(word) != 2:
                                continue

                            # Both characters must be kanji (in KRAD_MAP)
                            if word[0] not in KRAD_MAP or word[1] not in KRAD_MAP:
                                continue

                            meaning = ""
                            if senses and senses[0].get('english_definitions'):
                                meaning = ', '.join(senses[0]['english_definitions'][:3])

                            candidates.append((word, reading, meaning))
                            break  # One per entry

                    if candidates:
                        return random.choice(candidates)

        except Exception:
            continue

    return None


async def validate_jukugo_guess(word):
    """Validate that a guess is a valid 2-kanji noun"""
    if len(word) != 2:
        return False, "Guess must be exactly 2 kanji / 2文字の漢字を入力してください"

    # Check both are kanji
    for char in word:
        if char not in KRAD_MAP:
            return False, f"'{char}' is not a recognized kanji / 認識できない漢字です"

    # Validate it's a real word via Jisho
    is_valid, reading, meaning = await lookup_word(word)
    if not is_valid:
        return False, f"'{word}' is not a valid noun / 有効な名詞ではありません"

    return True, None


async def handle_waaduru_guess(message, guess_word):
    """Handle a Waaduru guess from a message"""
    channel_id = message.channel.id
    game = active_waaduru_games.get(channel_id)

    if not game or game.is_game_over():
        return

    # Validate the guess is a real word
    valid, error = await validate_jukugo_guess(guess_word)
    if not valid:
        await message.add_reaction("❓")
        await message.reply(error, delete_after=5)
        return

    # Check the guess
    results = game.check_guess(guess_word)
    if results is None:
        return

    # Add to game history
    game.add_guess(guess_word, results)

    # Create result display
    result_line = format_waaduru_result(guess_word, results)

    if game.solved:
        await message.add_reaction("🎉")
        active_waaduru_games.pop(channel_id, None)
        embed = create_waaduru_embed(game)
        await message.reply(embed=embed)
    elif game.is_game_over():
        await message.add_reaction("💀")
        active_waaduru_games.pop(channel_id, None)
        embed = create_waaduru_embed(game)
        await message.reply(embed=embed)
    else:
        # Show feedback with colored emoji (extract just the result type from tuples)
        result_types = [r for r, _ in results]
        if all(r == GuessResult.GREEN for r in result_types):
            await message.add_reaction("🎉")
        elif GuessResult.GREEN in result_types:
            await message.add_reaction("🟩")
        elif GuessResult.YELLOW in result_types:
            await message.add_reaction("🟨")
        elif GuessResult.ORANGE in result_types:
            await message.add_reaction("🟧")
        else:
            await message.add_reaction("⬛")

        embed = create_waaduru_embed(game)
        await message.reply(embed=embed)


async def handle_daily_waaduru_guess(message, guess_word):
    """Handle a Daily Waaduru guess - delete message, send private result, update public"""
    user_id = message.author.id
    game = active_daily_waaduru_games.get(user_id)

    if not game or game.is_game_over():
        return

    # Delete the user's message immediately to hide their guess
    try:
        await message.delete()
    except discord.errors.Forbidden:
        pass  # Can't delete, continue anyway

    # Validate the guess is a real word
    valid, error = await validate_jukugo_guess(guess_word)
    if not valid:
        # Send error privately via DM
        try:
            await message.author.send(f"❓ {error}")
        except discord.errors.Forbidden:
            # Can't DM, send ephemeral-like message that deletes
            await message.channel.send(
                f"{message.author.mention} ❓ {error}",
                delete_after=5
            )
        return

    # Check the guess
    results = game.check_guess(guess_word)
    if results is None:
        return

    # Add to game history
    game.add_guess(guess_word, results)

    # Send private result to user via DM
    private_embed = create_daily_private_embed(game, guess_word, results)
    try:
        await message.author.send(embed=private_embed)
    except discord.errors.Forbidden:
        # Can't DM user
        pass

    # Update the public message with colors only
    try:
        channel = bot.get_channel(game.channel_id)
        if channel:
            public_msg = await channel.fetch_message(game.message_id)
            public_embed = create_daily_public_embed(game, message.author.display_name)
            await public_msg.edit(embed=public_embed)
    except Exception as e:
        print(f"Failed to update public daily waaduru message: {e}")

    # If game is over, keep it in memory so user can't play again today
    # but mark it as complete


@bot.tree.command(name="waaduru", description="Play Waaduru - guess the 2-kanji word! / ワードル風漢字ゲーム")
@app_commands.describe(mode="Game mode: random or daily / ゲームモード：ランダムまたはデイリー")
@app_commands.choices(mode=[
    app_commands.Choice(name="Random (new word each game)", value="random"),
    app_commands.Choice(name="Daily (same word for everyone today)", value="daily"),
])
async def waaduru(interaction: discord.Interaction, mode: str = "random"):
    channel_id = interaction.channel_id
    user_id = interaction.user.id

    if mode == "daily":
        # Check if user already has an active daily game today
        if user_id in active_daily_waaduru_games:
            existing_game = active_daily_waaduru_games[user_id]
            # Check if it's from today
            if existing_game.is_game_over():
                await interaction.response.send_message(
                    "You've already completed today's Daily Waaduru! Come back tomorrow.\n"
                    "今日のデイリーワードルはすでに終了しました！明日また挑戦してください。",
                    ephemeral=True
                )
                return
            else:
                await interaction.response.send_message(
                    "You already have a Daily Waaduru in progress! Type your guess in chat.\n"
                    "デイリーワードルが進行中です！チャットに予想を入力してください。",
                    ephemeral=True
                )
                return

        await interaction.response.defer()

        # Get the daily word
        result = await get_daily_waaduru_word()

        if not result:
            await interaction.followup.send(
                "Failed to find today's word. Please try again.\n"
                "今日の単語が見つかりませんでした。もう一度お試しください。"
            )
            return

        answer_word, answer_reading, answer_meaning = result
        japan_date = datetime.now(JAPAN_TZ).strftime("%Y年%m月%d日")

        # Create the public progress message
        embed = discord.Embed(
            title=f"📝 Daily Waaduru - {interaction.user.display_name}",
            description=(
                f"**Daily Challenge / 今日のチャレンジ - {japan_date}**\n\n"
                "⬜⬜\n⬜⬜\n⬜⬜\n⬜⬜\n⬜⬜\n\n"
                "*Guesses are hidden - only colors shown publicly*\n"
                "*予想は非公開 - 色のみ表示されます*"
            ),
            color=discord.Color.blue()
        )

        public_msg = await interaction.followup.send(embed=embed)

        # Create the game
        game = DailyWaaduruGame(
            user_id, answer_word, answer_reading, answer_meaning,
            channel_id, public_msg.id
        )
        active_daily_waaduru_games[user_id] = game

        # Send private instructions
        private_embed = discord.Embed(
            title="📝 Daily Waaduru Started! / デイリーワードル開始！",
            description=(
                "**Type your 2-kanji guess in this channel.**\n"
                "**このチャンネルに2文字の漢字を入力してください。**\n\n"
                "Your message will be deleted and you'll receive the result privately.\n"
                "あなたのメッセージは削除され、結果はプライベートで送信されます。\n\n"
                "🟩 = Correct position / 正しい位置\n"
                "🟨 = Wrong position / 違う位置\n"
                "🟧 = Shared radical / 部首が共通\n"
                "⬛ = No match / 一致なし\n\n"
                "＿ ＿  ⬜⬜\n"
                "＿ ＿  ⬜⬜\n"
                "＿ ＿  ⬜⬜\n"
                "＿ ＿  ⬜⬜\n"
                "＿ ＿  ⬜⬜"
            ),
            color=discord.Color.purple()
        )
        await interaction.user.send(embed=private_embed)

    else:
        # Regular random mode
        if channel_id in active_waaduru_games:
            await interaction.response.send_message(
                "A Waaduru game is already running! Use `/endwaaduru` to end it.\n"
                "すでにゲームが進行中です！`/endwaaduru`で終了できます。",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        # Get a random jukugo
        result = await get_random_jukugo()

        if not result:
            await interaction.followup.send(
                "Failed to find a word. Please try again.\n"
                "単語が見つかりませんでした。もう一度お試しください。"
            )
            return

        answer_word, answer_reading, answer_meaning = result

        game = WaaduruGame(channel_id, answer_word, answer_reading, answer_meaning)
        active_waaduru_games[channel_id] = game

        embed = discord.Embed(
            title="📝 Waaduru - Kanji Wordle / 漢字ワードル",
            description=(
                "**Guess the 2-kanji word in 5 tries!**\n"
                "**5回以内に2文字の熟語を当ててください！**\n\n"
                "Type your guess in chat / チャットに予想を入力\n\n"
                "🟩 = Correct kanji, correct position / 正しい漢字、正しい位置\n"
                "🟨 = Correct kanji, wrong position / 正しい漢字、違う位置\n"
                "🟧 = Shared radical / 部首が共通\n"
                "⬛ = No match / 一致なし\n\n"
                "＿ ＿  ⬜⬜\n"
                "＿ ＿  ⬜⬜\n"
                "＿ ＿  ⬜⬜\n"
                "＿ ＿  ⬜⬜\n"
                "＿ ＿  ⬜⬜"
            ),
            color=discord.Color.blue()
        )

        await interaction.followup.send(embed=embed)


@bot.tree.command(name="endwaaduru", description="End the current Waaduru game / ワードルを終了")
async def endwaaduru(interaction: discord.Interaction):
    channel_id = interaction.channel_id

    if channel_id not in active_waaduru_games:
        await interaction.response.send_message(
            "No Waaduru game is running.\nワードルが進行していません。",
            ephemeral=True
        )
        return

    game = active_waaduru_games.pop(channel_id)

    embed = discord.Embed(
        title="🛑 Waaduru Ended / ワードル終了",
        description=(
            f"**Answer / 答え:** {game.answer_word} ({game.answer_reading})\n"
            f"**Meaning / 意味:** {game.answer_meaning}\n\n"
            f"Guesses made / 予想回数: {len(game.guesses)}/{game.max_guesses}"
        ),
        color=discord.Color.orange()
    )

    await interaction.response.send_message(embed=embed)


# ============ Kanji Puzzle Game ============

class KanjiPuzzleGame:
    def __init__(self, channel_id, answer_word, answer_reading, answer_meaning):
        self.channel_id = channel_id
        self.answer_word = answer_word
        self.answer_reading = answer_reading
        self.answer_meaning = answer_meaning
        self.guesses = []
        self.max_guesses = 5
        self.solved = False
        # Get radicals for each kanji in the answer
        self.radicals = [KRAD_MAP.get(k, set()) for k in answer_word]

    def get_radicals_display(self):
        """Format the radicals hint for display"""
        parts = []
        for i, rads in enumerate(self.radicals):
            rad_str = " ".join(sorted(rads)) if rads else "?"
            parts.append(f"**{i + 1}:** ({rad_str})")
        return "\n".join(parts)

    def check_guess(self, guess_word):
        """Check if guess is correct, return (is_correct, feedback)"""
        if guess_word == self.answer_word:
            return True, "🎉 Correct!"

        # Give feedback on which kanji are correct
        feedback = []
        for i, (guess_k, answer_k) in enumerate(zip(guess_word, self.answer_word)):
            if guess_k == answer_k:
                feedback.append(f"{guess_k} ✓")
            else:
                feedback.append(f"{guess_k} ✗")
        return False, " ".join(feedback)

    def add_guess(self, guess_word, feedback):
        self.guesses.append((guess_word, feedback))
        if guess_word == self.answer_word:
            self.solved = True

    def is_game_over(self):
        return self.solved or len(self.guesses) >= self.max_guesses

    def get_remaining_guesses(self):
        return self.max_guesses - len(self.guesses)


active_kanjipuzzle_games = {}


def create_kanjipuzzle_embed(game):
    """Create an embed showing the kanji puzzle state"""
    description = f"**Radicals / 部首:**\n{game.get_radicals_display()}\n\n"

    if game.guesses:
        description += "**Guesses / 予想:**\n"
        for guess_word, feedback in game.guesses:
            description += f"{guess_word} → {feedback}\n"

    description += f"\n**Remaining / 残り:** {game.get_remaining_guesses()} guesses"

    if game.solved:
        title = "🎉 Kanji Puzzle - Solved! / 正解！"
        color = discord.Color.green()
        description += f"\n\n**Answer / 答え:** {game.answer_word} ({game.answer_reading})\n**Meaning / 意味:** {game.answer_meaning}"
    elif game.is_game_over():
        title = "💀 Kanji Puzzle - Game Over / ゲームオーバー"
        color = discord.Color.red()
        description += f"\n\n**Answer / 答え:** {game.answer_word} ({game.answer_reading})\n**Meaning / 意味:** {game.answer_meaning}"
    else:
        title = f"🧩 Kanji Puzzle ({len(game.guesses)}/{game.max_guesses})"
        color = discord.Color.purple()

    return discord.Embed(title=title, description=description, color=color)


async def handle_kanjipuzzle_guess(message, guess_word):
    """Handle a Kanji Puzzle guess"""
    channel_id = message.channel.id
    game = active_kanjipuzzle_games.get(channel_id)

    if not game or game.is_game_over():
        return

    # Validate the guess is a real 2-kanji noun
    valid, error = await validate_jukugo_guess(guess_word)
    if not valid:
        await message.add_reaction("❓")
        await message.reply(error, delete_after=5)
        return

    # Check the guess
    is_correct, feedback = game.check_guess(guess_word)
    game.add_guess(guess_word, feedback)

    if game.solved:
        await message.add_reaction("🎉")
        active_kanjipuzzle_games.pop(channel_id, None)
    elif game.is_game_over():
        await message.add_reaction("💀")
        active_kanjipuzzle_games.pop(channel_id, None)
    else:
        if is_correct:
            await message.add_reaction("🎉")
        else:
            await message.add_reaction("❌")

    embed = create_kanjipuzzle_embed(game)
    await message.reply(embed=embed)


@bot.tree.command(name="kanjipuzzle", description="Guess the word from its radicals! / 部首から熟語を当てよう")
async def kanjipuzzle(interaction: discord.Interaction):
    channel_id = interaction.channel_id

    if channel_id in active_kanjipuzzle_games:
        await interaction.response.send_message(
            "A Kanji Puzzle is already running! Use `/endkanjipuzzle` to end it.\n"
            "すでにゲームが進行中です！`/endkanjipuzzle`で終了できます。",
            ephemeral=True
        )
        return

    await interaction.response.defer()

    # Get a random jukugo (reuse the function from Waaduru)
    result = await get_random_jukugo()

    if not result:
        await interaction.followup.send(
            "Failed to find a word. Please try again.\n"
            "単語が見つかりませんでした。もう一度お試しください。"
        )
        return

    answer_word, answer_reading, answer_meaning = result

    game = KanjiPuzzleGame(channel_id, answer_word, answer_reading, answer_meaning)
    active_kanjipuzzle_games[channel_id] = game

    embed = discord.Embed(
        title="🧩 Kanji Puzzle / 漢字パズル",
        description=(
            "**Guess the 2-kanji word from its radicals!**\n"
            "**部首から2文字の熟語を当ててください！**\n\n"
            f"**Radicals / 部首:**\n{game.get_radicals_display()}\n\n"
            f"You have **{game.max_guesses}** guesses. Type your answer in chat!\n"
            f"**{game.max_guesses}**回以内に当ててください。チャットに答えを入力！"
        ),
        color=discord.Color.purple()
    )

    await interaction.followup.send(embed=embed)


@bot.tree.command(name="endkanjipuzzle", description="End the current Kanji Puzzle / 漢字パズルを終了")
async def endkanjipuzzle(interaction: discord.Interaction):
    channel_id = interaction.channel_id

    if channel_id not in active_kanjipuzzle_games:
        await interaction.response.send_message(
            "No Kanji Puzzle is running.\n漢字パズルが進行していません。",
            ephemeral=True
        )
        return

    game = active_kanjipuzzle_games.pop(channel_id)

    embed = discord.Embed(
        title="🛑 Kanji Puzzle Ended / 漢字パズル終了",
        description=(
            f"**Answer / 答え:** {game.answer_word} ({game.answer_reading})\n"
            f"**Meaning / 意味:** {game.answer_meaning}\n\n"
            f"Guesses made / 予想回数: {len(game.guesses)}/{game.max_guesses}"
        ),
        color=discord.Color.orange()
    )

    await interaction.response.send_message(embed=embed)


# ============ Kanji Lookup ============

async def get_kanji_info(kanji):
    """Fetch kanji information from kanjiapi.dev"""
    url = f"https://kanjiapi.dev/v1/kanji/{kanji}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return None
                return await response.json()
    except Exception:
        return None


def get_stroke_order_gif_url(kanji):
    """Get the stroke order GIF URL for a kanji"""
    # Convert kanji to unicode hex (lowercase, no prefix)
    unicode_hex = format(ord(kanji), 'x')
    return f"https://raw.githubusercontent.com/mistval/kanji_images/master/gifs/{unicode_hex}.gif"


@bot.tree.command(name="kanji", description="Look up detailed kanji information / 漢字の詳細を調べる")
@app_commands.describe(kanji="The kanji character to look up / 調べたい漢字")
async def kanji_lookup(interaction: discord.Interaction, kanji: str):
    # Validate input - must be a single kanji
    if len(kanji) != 1:
        await interaction.response.send_message(
            "Please enter a single kanji character.\n1文字の漢字を入力してください。",
            ephemeral=True
        )
        return

    # Check if it's a kanji (exists in KRAD_MAP)
    if kanji not in KRAD_MAP:
        await interaction.response.send_message(
            f"'{kanji}' is not a recognized kanji.\n認識できない漢字です。",
            ephemeral=True
        )
        return

    await interaction.response.defer()

    # Fetch data from kanjiapi.dev
    kanji_data = await get_kanji_info(kanji)

    # Get radicals from KRAD_MAP
    radicals = KRAD_MAP.get(kanji, set())
    parts_str = " ".join(sorted(radicals)) if radicals else "N/A"

    # Build the embed
    if kanji_data:
        stroke_count = kanji_data.get('stroke_count', 'N/A')
        meanings = kanji_data.get('meanings', [])
        on_readings = kanji_data.get('on_readings', [])
        kun_readings = kanji_data.get('kun_readings', [])
        grade = kanji_data.get('grade')
        jlpt = kanji_data.get('jlpt')
        freq = kanji_data.get('freq_mainichi_shinbun')

        meanings_str = ", ".join(meanings) if meanings else "N/A"
        on_str = "、".join(on_readings) if on_readings else "N/A"
        kun_str = "、".join(kun_readings) if kun_readings else "N/A"

        description = f"**{stroke_count}** strokes\n\n"
        description += f"**Parts / 部首:** {parts_str}\n\n"
        description += f"**Meaning / 意味:**\n{meanings_str}\n\n"
        description += f"**On'yomi / 音読み:** {on_str}\n"
        description += f"**Kun'yomi / 訓読み:** {kun_str}\n"

        # Additional info
        extra_info = []
        if grade:
            extra_info.append(f"Grade {grade} kanji")
        if jlpt:
            extra_info.append(f"JLPT N{jlpt}")
        if freq:
            extra_info.append(f"#{freq} in newspapers")

        if extra_info:
            description += f"\n{' • '.join(extra_info)}"
    else:
        # Fallback if API fails - just show parts
        description = f"**Parts / 部首:** {parts_str}\n\n"
        description += "*Could not fetch additional data*"

    embed = discord.Embed(
        title=kanji,
        description=description,
        color=discord.Color.teal()
    )

    # Add stroke order GIF
    gif_url = get_stroke_order_gif_url(kanji)
    embed.set_image(url=gif_url)

    await interaction.followup.send(embed=embed)


# ============ Pitch Accent Lookup ============

async def get_pitch_accent(word):
    """Fetch pitch accent data from Jotoba API"""
    url = "https://jotoba.de/api/search/words"

    payload = {
        "query": word,
        "language": "English",
        "no_english": False
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    return None
                data = await response.json()

                if not data.get('words'):
                    return None

                return data['words'][0]  # Return first result
    except Exception as e:
        print(f"Pitch accent lookup error: {e}")
        return None


def format_pitch_display(pitch_data):
    """
    Format pitch accent data for visual display.
    Returns a string showing high/low pitch pattern.
    """
    if not pitch_data:
        return None

    # Build visual representation
    # High pitch = ￣ (overline/high), Low pitch = ＿ (low)
    parts = []
    visual = []

    for part in pitch_data:
        mora = part.get('part', '')
        is_high = part.get('high', False)

        parts.append(mora)
        if is_high:
            visual.append('ˉ' * len(mora))  # High mark
        else:
            visual.append('ˍ' * len(mora))  # Low mark

    reading = ''.join(parts)
    pitch_line = ''.join(visual)

    return reading, pitch_line


def create_pitch_visual(pitch_data):
    """Create a text-based pitch accent visualization"""
    if not pitch_data:
        return "No pitch data available"

    result_lines = []
    reading_chars = []
    heights = []

    for part in pitch_data:
        mora = part.get('part', '')
        is_high = part.get('high', False)
        for char in mora:
            reading_chars.append(char)
            heights.append(is_high)

    # Create visual with boxes
    high_line = ""
    low_line = ""
    for i, (char, is_high) in enumerate(zip(reading_chars, heights)):
        if is_high:
            high_line += f"[{char}]"
            low_line += "   "
        else:
            high_line += "   "
            low_line += f"[{char}]"

    # Simpler visual: show pattern with markers
    pattern = ""
    for i, (char, is_high) in enumerate(zip(reading_chars, heights)):
        if is_high:
            pattern += f" {char}̄"  # Character with macron
        else:
            pattern += f" {char}"

    # Create line-based visual
    line_visual = ""
    for i, (char, is_high) in enumerate(zip(reading_chars, heights)):
        prev_high = heights[i-1] if i > 0 else False
        next_high = heights[i+1] if i < len(heights)-1 else False

        if is_high:
            line_visual += "▔"
        else:
            line_visual += "▁"

    return {
        'reading': ''.join(reading_chars),
        'pattern': line_visual,
        'heights': heights
    }


@bot.tree.command(name="pitch", description="Look up pitch accent for a Japanese word / 単語のアクセントを調べる")
@app_commands.describe(word="The word to look up / 調べたい単語")
async def pitch_lookup(interaction: discord.Interaction, word: str):
    await interaction.response.defer()

    # Fetch from Jotoba API
    result = await get_pitch_accent(word)

    if not result:
        embed = discord.Embed(
            title="❌ Not Found / 見つかりません",
            description=f"Could not find pitch accent data for **{word}**\n「**{word}**」のアクセント情報が見つかりません",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed)
        return

    # Extract data
    reading = result.get('reading', {})
    kana = reading.get('kana', word)
    kanji_reading = reading.get('kanji', '')

    pitch_data = result.get('pitch', [])
    meanings = result.get('senses', [])
    audio_url = result.get('audio')

    # Get meaning
    meaning_str = "N/A"
    if meanings and meanings[0].get('glosses'):
        meaning_str = ", ".join(meanings[0]['glosses'][:3])

    # Build description
    if kanji_reading:
        title = f"🎵 {kanji_reading} ({kana})"
    else:
        title = f"🎵 {kana}"

    description = f"**Meaning / 意味:** {meaning_str}\n\n"

    # Create pitch visualization
    if pitch_data:
        visual = create_pitch_visual(pitch_data)
        reading_display = visual['reading']
        pattern = visual['pattern']
        heights = visual['heights']

        # Create a nicer visual with the pattern
        description += "**Pitch Accent / アクセント:**\n"
        description += f"`{pattern}`\n"
        description += f"`{reading_display}`\n\n"

        # Show pattern type
        # Determine accent type based on pattern
        if all(h == False for h in heights):
            accent_type = "平板型 (Heiban/Flat)"
        elif heights[0] == False and any(heights[1:]):
            if heights[-1] == True:
                accent_type = "平板型 (Heiban/Flat)"
            else:
                # Find where it drops
                drop_pos = None
                for i in range(1, len(heights)):
                    if heights[i-1] == True and heights[i] == False:
                        drop_pos = i
                        break
                if drop_pos:
                    accent_type = f"起伏型 (Kifuku) - drops after mora {drop_pos}"
                else:
                    accent_type = "起伏型 (Kifuku)"
        elif heights[0] == True:
            accent_type = "頭高型 (Atamadaka/Head-high)"
        else:
            accent_type = "Unknown pattern"

        description += f"**Type / 型:** {accent_type}\n"

        # Legend
        description += "\n`▔` = High pitch / 高  `▁` = Low pitch / 低"
    else:
        description += "*No pitch accent data available*\n*アクセント情報がありません*"

    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.purple()
    )

    # Add audio if available
    if audio_url:
        # Jotoba audio URLs are relative, need full path
        full_audio_url = f"https://jotoba.de{audio_url}" if audio_url.startswith('/') else audio_url
        embed.add_field(
            name="Audio / 音声",
            value=f"[Listen / 聞く]({full_audio_url})",
            inline=False
        )

    embed.set_footer(text="Data from Jotoba.de")

    await interaction.followup.send(embed=embed)


# ============ Jisho Word Lookup ============

async def jisho_search(query):
    """Search Jisho for a word and return results"""
    import urllib.parse
    encoded_query = urllib.parse.quote(query)
    url = f"https://jisho.org/api/v1/search/words?keyword={encoded_query}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return None
                return await response.json()
    except Exception:
        return None


def format_jisho_entry(entry, index=1):
    """Format a single Jisho entry for display"""
    japanese = entry.get('japanese', [])
    senses = entry.get('senses', [])

    if not japanese:
        return None

    # Get the main word and reading
    main_jp = japanese[0]
    word = main_jp.get('word', '')
    reading = main_jp.get('reading', '')

    if word and reading:
        word_display = f"**{word}** ({reading})"
    elif word:
        word_display = f"**{word}**"
    else:
        word_display = f"**{reading}**"

    # Get definitions
    definitions = []
    for i, sense in enumerate(senses[:3], 1):  # Limit to 3 senses
        eng_defs = sense.get('english_definitions', [])
        parts = sense.get('parts_of_speech', [])

        if eng_defs:
            def_text = ", ".join(eng_defs)
            if parts:
                parts_text = ", ".join(p for p in parts if p)
                def_text = f"*{parts_text}* — {def_text}"
            definitions.append(f"{i}. {def_text}")

    return {
        'word_display': word_display,
        'definitions': definitions,
        'word': word or reading,
        'reading': reading
    }


@bot.tree.command(name="jisho", description="Look up a word in Jisho dictionary / 辞書で単語を検索")
@app_commands.describe(word="The word to look up (Japanese or English) / 検索する単語")
async def jisho_lookup(interaction: discord.Interaction, word: str):
    await interaction.response.defer()

    # Search Jisho
    data = await jisho_search(word)

    if not data or not data.get('data'):
        embed = discord.Embed(
            title="❌ No Results / 結果なし",
            description=f"No results found for **{word}**\n「**{word}**」の検索結果はありません",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed)
        return

    results = data['data']

    # Format the first result in detail
    first_entry = format_jisho_entry(results[0])

    if not first_entry:
        embed = discord.Embed(
            title="❌ Error / エラー",
            description="Could not parse results",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed)
        return

    # Build description
    description = f"{first_entry['word_display']}\n\n"
    description += "\n".join(first_entry['definitions'])

    # Show additional results preview if available
    if len(results) > 1:
        description += "\n\n**Other results / 他の結果:**\n"
        for entry in results[1:4]:  # Show up to 3 more
            formatted = format_jisho_entry(entry)
            if formatted:
                # Just show word and first definition
                first_def = formatted['definitions'][0] if formatted['definitions'] else ""
                # Truncate if too long
                if len(first_def) > 60:
                    first_def = first_def[:57] + "..."
                description += f"• {formatted['word_display']}: {first_def}\n"

    # Add Jisho link
    import urllib.parse
    jisho_url = f"https://jisho.org/search/{urllib.parse.quote(word)}"
    description += f"\n[**Show more on Jisho / Jishoで詳しく見る →**]({jisho_url})"

    embed = discord.Embed(
        title=f"📖 {word}",
        description=description,
        color=discord.Color.orange(),
        url=jisho_url
    )

    await interaction.followup.send(embed=embed)


# ============ Translation ============

async def translate_text(text, source_lang, target_lang):
    """Translate text using MyMemory API"""
    import urllib.parse
    encoded_text = urllib.parse.quote(text)
    url = f"https://api.mymemory.translated.net/get?q={encoded_text}&langpair={source_lang}|{target_lang}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return None, "API request failed"

                data = await response.json()

                if data.get('responseStatus') != 200:
                    return None, data.get('responseDetails', 'Translation failed')

                translated = data.get('responseData', {}).get('translatedText', '')
                return translated, None
    except Exception as e:
        return None, str(e)


def detect_language_for_translation(text):
    """Detect if text is Japanese or English and return appropriate lang pair"""
    jp_ratio, en_ratio = calculate_language_ratio(text)

    if jp_ratio > en_ratio:
        return "ja", "en", "Japanese → English"
    else:
        return "en", "ja", "English → Japanese"


@bot.tree.command(name="translate", description="Translate text between Japanese and English / 日英翻訳")
@app_commands.describe(text="Text to translate, or 'last' to translate the previous message / 翻訳するテキスト、または'last'で直前のメッセージを翻訳")
async def translate(interaction: discord.Interaction, text: str):
    await interaction.response.defer()

    original_text = text

    # Handle "last" to translate previous message
    if text.lower() == "last":
        # Get the previous message in the channel
        try:
            messages = [msg async for msg in interaction.channel.history(limit=2)]
            # messages[0] might be a bot message, find the last non-bot message
            prev_message = None
            for msg in messages:
                if not msg.author.bot and msg.id != interaction.id:
                    prev_message = msg
                    break

            if not prev_message:
                # Try getting more messages
                messages = [msg async for msg in interaction.channel.history(limit=10)]
                for msg in messages:
                    if not msg.author.bot:
                        prev_message = msg
                        break

            if not prev_message:
                embed = discord.Embed(
                    title="❌ No Message Found / メッセージが見つかりません",
                    description="Could not find a previous message to translate.",
                    color=discord.Color.red()
                )
                await interaction.followup.send(embed=embed)
                return

            original_text = prev_message.content
            text = original_text

        except Exception as e:
            embed = discord.Embed(
                title="❌ Error / エラー",
                description=f"Could not fetch previous message: {e}",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
            return

    if not text.strip():
        embed = discord.Embed(
            title="❌ Empty Text / テキストが空です",
            description="Please provide text to translate.",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed)
        return

    # Detect language and translate
    source_lang, target_lang, direction = detect_language_for_translation(text)
    translated, error = await translate_text(text, source_lang, target_lang)

    if error:
        embed = discord.Embed(
            title="❌ Translation Failed / 翻訳失敗",
            description=f"Error: {error}",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed)
        return

    # Truncate original if too long
    display_original = original_text
    if len(display_original) > 500:
        display_original = display_original[:497] + "..."

    embed = discord.Embed(
        title=f"🌐 {direction}",
        color=discord.Color.blue()
    )
    embed.add_field(name="Original / 原文", value=display_original, inline=False)
    embed.add_field(name="Translation / 翻訳", value=translated, inline=False)

    await interaction.followup.send(embed=embed)


# ============ Immersion Mode Commands ============

immersion_group = app_commands.Group(name="immersion", description="Immersion mode settings / 没入モード設定")


@immersion_group.command(name="jp", description="Enable Japanese immersion mode / 日本語没入モードを有効化")
@app_commands.default_permissions(manage_channels=True)
async def immersion_jp(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    immersion_channels[channel_id] = "jp"

    embed = discord.Embed(
        title="🇯🇵 Japanese Immersion Mode Enabled / 日本語没入モード有効",
        description=(
            "This channel now requires **Japanese** text.\n"
            "このチャンネルでは**日本語**が必要になりました。\n\n"
            f"• Up to **{MAX_ENGLISH_WORDS_JP_MODE}** English words allowed\n"
            f"• 英語は**{MAX_ENGLISH_WORDS_JP_MODE}単語**まで許可\n"
            "• Internet terms (www, lol, etc.) don't count\n"
            "• ネットスラング（www、lol等）は除外\n\n"
            "Use `/immersion disable` to turn off.\n"
            "`/immersion disable`で無効化できます。"
        ),
        color=discord.Color.red()
    )
    await interaction.response.send_message(embed=embed)


@immersion_group.command(name="en", description="Enable English immersion mode / 英語没入モードを有効化")
@app_commands.default_permissions(manage_channels=True)
async def immersion_en(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    immersion_channels[channel_id] = "en"

    embed = discord.Embed(
        title="🇬🇧 English Immersion Mode Enabled / 英語没入モード有効",
        description=(
            "This channel now requires **English** text.\n"
            "このチャンネルでは**英語**が必要になりました。\n\n"
            f"• Up to **{MAX_JAPANESE_CHUNKS_EN_MODE}** Japanese expressions allowed\n"
            f"• 日本語は**{MAX_JAPANESE_CHUNKS_EN_MODE}個**まで許可\n\n"
            "Use `/immersion disable` to turn off.\n"
            "`/immersion disable`で無効化できます。"
        ),
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed)


@immersion_group.command(name="disable", description="Disable immersion mode / 没入モードを無効化")
@app_commands.default_permissions(manage_channels=True)
async def immersion_disable(interaction: discord.Interaction):
    channel_id = interaction.channel_id

    if channel_id in immersion_channels:
        del immersion_channels[channel_id]
        embed = discord.Embed(
            title="✅ Immersion Mode Disabled / 没入モード無効化",
            description=(
                "Immersion mode has been turned off for this channel.\n"
                "このチャンネルの没入モードを無効化しました。"
            ),
            color=discord.Color.green()
        )
    else:
        embed = discord.Embed(
            title="ℹ️ Not Active / 無効",
            description=(
                "Immersion mode was not active in this channel.\n"
                "このチャンネルでは没入モードは有効ではありませんでした。"
            ),
            color=discord.Color.gray()
        )

    await interaction.response.send_message(embed=embed)


@immersion_group.command(name="status", description="Check current immersion mode status / 現在の没入モード状態を確認")
async def immersion_status(interaction: discord.Interaction):
    channel_id = interaction.channel_id

    if channel_id in immersion_channels:
        mode = immersion_channels[channel_id]
        lang = "Japanese / 日本語" if mode == "jp" else "English / 英語"
        flag = "🇯🇵" if mode == "jp" else "🇬🇧"

        if mode == "jp":
            rules = f"Max {MAX_ENGLISH_WORDS_JP_MODE} English words allowed"
        else:
            rules = f"Max {MAX_JAPANESE_CHUNKS_EN_MODE} Japanese expressions allowed"

        embed = discord.Embed(
            title=f"{flag} Immersion Mode Active / 没入モード有効",
            description=(
                f"**Language / 言語:** {lang}\n"
                f"**Rules / ルール:** {rules}\n"
                f"• Internet terms (www, lol, ok, etc.) are ignored\n"
                f"• ネットスラングは除外されます"
            ),
            color=discord.Color.gold()
        )
    else:
        embed = discord.Embed(
            title="💤 Immersion Mode Inactive / 没入モード無効",
            description="No immersion mode is active in this channel.\nこのチャンネルでは没入モードは無効です。",
            color=discord.Color.gray()
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(immersion_group)


if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_TOKEN not found in .env file")
        exit(1)
    bot.run(TOKEN)
