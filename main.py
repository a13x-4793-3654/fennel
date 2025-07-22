# main.py (起動ファイル) の修正案

from config import ADMIN_GUILD_ID, USER_GUILD_ID
import discord
from discord.ext import commands
import importlib
import pathlib

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# トークン読み込み
with open('.discord_token') as f:
    DISCORD_TOKEN = f.read().strip()

# ✅ コマンドの登録と同期を setup_hook() で完結させる
@bot.event
async def setup_hook():
    # 1. commandsフォルダからすべてのコマンドモジュールを読み込む
    #    この段階で、各コマンドは bot.tree に追加される
    commands_dir = pathlib.Path("commands")
    py_files = [f for f in commands_dir.glob("*.py") if f.name != "__init__.py"]

    for file in py_files:
        module_name = f"commands.{file.stem}"
        try:
            module = importlib.import_module(module_name)
            if hasattr(module, "setup"):
                await module.setup(bot)
                print(f"✅ コマンドモジュール '{module_name}' を読み込みました。")
            else:
                print(f"⚠️ モジュール '{module_name}' に setup(bot) が定義されていません。スキップします。")
        except Exception as e:
            print(f"❌ モジュール '{module_name}' の読み込み中にエラーが発生しました: {e}")

    # 2. ギルドごとにコマンドを同期する
    #    sync() が差分を自動で更新してくれるため、事前のclearは不要
    guild_ids = [ADMIN_GUILD_ID, USER_GUILD_ID]
    for guild_id in guild_ids:
        try:
            guild = discord.Object(id=guild_id)
            # guild引数を指定すると、そのギルドに紐づくコマンドのみを同期する
            synced = await bot.tree.sync(guild=guild)
            print(f"✅ ギルド {guild_id} に {len(synced)} 件のコマンドを同期しました。")
        except Exception as e:
            print(f"❌ ギルド {guild_id} の同期でエラーが発生しました: {e}")
    
    # 3. (任意) グローバルコマンドを同期する
    #    特定のギルドに紐付かないコマンド（全てのサーバーで使えるコマンド）を同期
    #    guild引数を指定しないことでグローバルコマンドが同期される
    # synced_global = await bot.tree.sync(guild=None)
    # print(f"🌐 グローバルに {len(synced_global)} 件のコマンドを同期しました。")


# 🔹 Bot 起動ログ
@bot.event
async def on_ready():
    print(f"🤖 ログインしました: {bot.user}（ID: {bot.user.id}）")

# 🔹 Bot 起動
bot.run(DISCORD_TOKEN)