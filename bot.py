import os
import random
import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")


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
    """Extract the first kana from text, normalizing small kana"""
    if not text:
        return ""
    for char in text:
        code = ord(char)
        if (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF):
            return normalize_small_kana(char)
    return ""


def get_last_kana(text):
    """Extract the last kana from text, skipping ー, normalizing small kana"""
    if not text:
        return ""
    for i in range(len(text) - 1, -1, -1):
        char = text[i]
        code = ord(char)
        if ((0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF)) and char != 'ー':
            return normalize_small_kana(char)
    return ""


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


async def lookup_word(word):
    """Look up a word using Jisho API, returns (is_valid, reading, meaning) or (False, None, None)"""
    url = f"https://jisho.org/api/v1/search/words?keyword={word}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return False, None, None

                data = await response.json()

                if not data.get('data'):
                    return False, None, None

                for entry in data['data']:
                    japanese = entry.get('japanese', [])
                    if not japanese:
                        continue

                    for jp in japanese:
                        entry_word = jp.get('word', '')
                        entry_reading = jp.get('reading', '')

                        if entry_word == word or entry_reading == word:
                            reading = entry_reading if entry_reading else word
                            senses = entry.get('senses', [])
                            meaning = ""
                            if senses and senses[0].get('english_definitions'):
                                meaning = ', '.join(senses[0]['english_definitions'][:3])
                            return True, reading, meaning

                return False, None, None
    except Exception:
        return False, None, None


async def find_bot_word(start_kana, used_words):
    """Find a word for the bot to play, starting with the given kana"""
    url = f"https://jisho.org/api/v1/search/words?keyword={start_kana}*"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return None, None, None

                data = await response.json()

                if not data.get('data'):
                    return None, None, None

                candidates = []

                for entry in data['data']:
                    japanese = entry.get('japanese', [])
                    if not japanese:
                        continue

                    for jp in japanese:
                        word = jp.get('word', '')
                        reading = jp.get('reading', '')

                        if not reading:
                            continue

                        first = normalize_kana(get_first_kana(reading))
                        if first != normalize_kana(start_kana):
                            continue

                        last = normalize_kana(get_last_kana(reading))
                        if last == "ん":
                            continue

                        if word in used_words or reading in used_words:
                            continue

                        senses = entry.get('senses', [])
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

ENGLISH_ROLES = {
    "beginner": "English - Beginner / 英語 - 初心者",
    "intermediate": "English - Intermediate / 英語 - 中級者",
    "fluent": "English - Fluent / 英語 - 上級者",
    "native": "English - Native / 英語 - ネイティブ",
}

JAPANESE_ROLES = {
    "beginner": "Japanese - Beginner / 日本語 - 初心者",
    "intermediate": "Japanese - Intermediate / 日本語 - 中級者",
    "fluent": "Japanese - Fluent / 日本語 - 上級者",
    "native": "Japanese - Native / 日本語 - ネイティブ",
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

    role_name = role_dict[level]
    role = discord.utils.get(guild.roles, name=role_name)

    if not role:
        await interaction.response.send_message(
            "Role not found. Please contact an administrator.",
            ephemeral=True,
        )
        return

    roles_to_remove = [
        discord.utils.get(guild.roles, name=name)
        for name in role_dict.values()
        if discord.utils.get(guild.roles, name=name) in member.roles
    ]

    if roles_to_remove:
        await member.remove_roles(*roles_to_remove)

    await member.add_roles(role)
    await interaction.response.send_message(
        f"Your role has been set to: **{role_name}**\nあなたのロールが設定されました。",
        ephemeral=True,
    )


class RoleAssignView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(EnglishLevelSelect())
        self.add_item(JapaneseLevelSelect())


class JapaneseLearningBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.add_view(RoleAssignView())
        await self.tree.sync()


bot = JapaneseLearningBot()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")


@bot.tree.command(name="ping", description="Check if the bot is responsive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"Pong! Latency: {round(bot.latency * 1000)}ms"
    )


@bot.tree.command(name="roleassign", description="Create the role assignment panel (Admin only)")
@app_commands.default_permissions(administrator=True)
async def roleassign(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild

    for role_name in list(ENGLISH_ROLES.values()) + list(JAPANESE_ROLES.values()):
        if not discord.utils.get(guild.roles, name=role_name):
            await guild.create_role(name=role_name)

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
            "🗾 Native / ネイティブ"
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
    if channel_id not in active_games:
        return

    content = message.content.strip()

    if not content:
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

    is_valid, reading, meaning = await lookup_word(content)

    if not is_valid:
        await message.add_reaction("❓")
        await message.reply(
            f"Word not found in dictionary / 辞書に見つかりません: **{content}**",
            delete_after=5
        )
        return

    first_kana = normalize_kana(get_first_kana(reading))
    required_kana = normalize_kana(game.current_kana)

    if first_kana != required_kana:
        await message.add_reaction("❌")
        await message.reply(
            f"Word must start with **{game.current_kana}** / 「**{game.current_kana}**」で始まる言葉を入力してください\n"
            f"Your word starts with: {get_first_kana(reading)}",
            delete_after=5
        )
        return

    last_kana = get_last_kana(reading)
    normalized_last = normalize_kana(last_kana)

    # Word Basket mode: also check end kana
    if game.mode == GameMode.WORD_BASKET:
        required_end = normalize_kana(game.end_kana)
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
    if game.mode != GameMode.WORD_BASKET and normalized_last == "ん":
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
        bot_normalized_last = normalize_kana(bot_last_kana)

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


if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_TOKEN not found in .env file")
        exit(1)
    bot.run(TOKEN)
