import discord
from discord import app_commands, Interaction
from discord.ext import commands
import aiohttp
from bs4 import BeautifulSoup
import re
import io

from config import ADMIN_GUILD_ID

# --------------------------------------------------------------------------------
# 🔹 ヘルパー関数
# --------------------------------------------------------------------------------
async def get_minecraft_uuid(username: str) -> dict:
    """
    Mojang APIを使用してMinecraftユーザー名からUUIDを取得します。
    成功時は{'uuid': '...', 'username': '...'}、失敗時は{'error': '...'}を返します。
    """
    url = f"https://api.mojang.com/users/profiles/minecraft/{username}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return {"uuid": data["id"], "username": data["name"]}
                elif response.status == 404:
                    return {"error": f"ユーザー `{username}` が見つかりませんでした。"}
                else:
                    return {"error": f"APIエラーが発生しました (ステータスコード: {response.status})。"}
    except aiohttp.ClientError as e:
        return {"error": f"APIへの接続中にエラーが発生しました: {e}"}

def create_progress_bar(value: int, max_value: int = 10) -> str:
    """
    評価値からプログレスバーの文字列を生成します。
    例: 7 -> "[■■■■■■■□□□]"
    """
    if not 0 <= value <= max_value:
        return "[評価値が無効です]"
    
    filled_blocks = '■' * value
    empty_blocks = '□' * (max_value - value)
    return f"[{filled_blocks}{empty_blocks}]"

# --------------------------------------------------------------------------------
# 🔹 スラッシュコマンドの実装
# --------------------------------------------------------------------------------
class McCommands(commands.GroupCog, name="mc"):
    """Minecraft関連の情報を検索するコマンド群"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    # --- 1. search_uuid サブコマンド ---
    @app_commands.command(name="search_uuid", description="Minecraftユーザー名からUUIDを検索します。")
    @app_commands.describe(username="Minecraftのユーザー名")
    async def search_uuid(self, interaction: Interaction, username: str):
        await interaction.response.defer()
        result = await get_minecraft_uuid(username)

        if "error" in result:
            await interaction.followup.send(f"❌ {result['error']}")
            return

        avatar_url = f"https://crafatar.com/renders/head/{result['uuid']}"
        avatar_filename = "avatar.png"
        avatar_file = None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(avatar_url) as resp:
                    if resp.status == 200:
                        image_data = await resp.read()
                        avatar_file = discord.File(io.BytesIO(image_data), filename=avatar_filename)
        except Exception as e:
            print(f"アバター画像のダウンロードに失敗しました: {e}")

        embed = discord.Embed(
            title=f"✅ UUID検索結果: `{result['username']}`",
            color=discord.Color.green()
        )
        embed.add_field(name="Username", value=result['username'], inline=False)
        embed.add_field(name="UUID", value=result['uuid'], inline=False)

        if avatar_file:
            embed.set_thumbnail(url=f"attachment://{avatar_filename}")
            await interaction.followup.send(embed=embed, file=avatar_file)
        else:
            await interaction.followup.send(embed=embed)


    # --- 2. search_global_bans サブコマンド ---
    @app_commands.command(name="search_global_bans", description="MCBansのグローバルBan履歴を検索します。")
    @app_commands.describe(username="Minecraftのユーザー名またはUUID")
    async def search_global_bans(self, interaction: Interaction, username: str):
        await interaction.response.defer()

        uuid = None
        if re.match(r"^[0-9a-fA-F]{32}$", username):
            uuid = username
        else:
            uuid_result = await get_minecraft_uuid(username)
            if "error" in uuid_result:
                await interaction.followup.send(f"❌ {uuid_result['error']}")
                return
            uuid = uuid_result["uuid"]
            username = uuid_result["username"]

        avatar_url = f"https://crafatar.com/renders/head/{uuid}"
        avatar_filename = "avatar.png"
        avatar_file = None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(avatar_url) as resp:
                    if resp.status == 200:
                        image_data = await resp.read()
                        avatar_file = discord.File(io.BytesIO(image_data), filename=avatar_filename)
        except Exception as e:
            print(f"アバター画像のダウンロードに失敗しました: {e}")

        mcbans_url = f"https://mcbans.com/player/{uuid}/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(mcbans_url, headers=headers) as response:
                    if response.status != 200:
                        await interaction.followup.send(f"❌ MCBansへのアクセスに失敗しました (ステータスコード: {response.status})。")
                        return
                    html = await response.text()

            soup = BeautifulSoup(html, "html.parser")
            
            reputation_text = "N/A"
            reputation_bar = ""
            try:
                selector = "#content > div > div.box-holder-one-third > div > div > fieldset > section:nth-child(2) > div"
                reputation_element = soup.select_one(selector)
                if reputation_element:
                    raw_text = reputation_element.get_text(strip=True)
                    reputation_text = raw_text
                    value = int(raw_text.split('/')[0].strip())
                    reputation_bar = create_progress_bar(value)
            except Exception as e:
                print(f"Reputationの解析中にエラーが発生しました: {e}")

            table_bodies = soup.find_all("tbody")
            ban_list = []
            for tbody in table_bodies:
                rows = tbody.find_all("tr")
                for row in rows:
                    cols = row.find_all("td")
                    if len(cols) < 6: continue
                    scope = cols[0].get_text(strip=True)
                    server = cols[2].get_text(strip=True)
                    reason = cols[4].get_text(strip=True)
                    date = cols[5].get_text(strip=True) # 日付も取得
                    if "Global" in scope or "Local" in scope:
                        ban_list.append({"scope": scope, "server": server, "reason": reason, "date": date})

            # Embedの作成
            if not ban_list:
                embed = discord.Embed(
                    title=f"Ban履歴: `{username}`",
                    description="このプレイヤーにBanの履歴はありませんでした。",
                    color=discord.Color.green()
                )
            else:
                embed = discord.Embed(
                    title=f"Ban履歴: `{username}`",
                    color=discord.Color.red()
                )
                # ▼▼▼ 修正: Ban履歴を個別フィールドで表示 ▼▼▼
                for i, ban in enumerate(ban_list):
                    # Embedのフィールド数制限(25)を超えないようにする
                    if i >= 5:
                        embed.add_field(name="...", value="ほかにもBan履歴があります。", inline=False)
                        break
                    
                    field_name = f"📜 {ban['scope']} Ban (#{i+1})"
                    field_value = (
                        f"**サーバー:** {ban['server']}\n"
                        f"**理　　由:** {ban['reason']}\n"
                        f"**日　　付:** {ban['date']}"
                    )
                    embed.add_field(name=field_name, value=field_value, inline=False)
            
            # 評価値とプロフィールを末尾に追加
            embed.add_field(name="評価値", value=f"{reputation_bar} {reputation_text}", inline=False)
            embed.add_field(name="MCBans Profile", value=mcbans_url, inline=False)

            # メッセージ送信
            if avatar_file:
                embed.set_thumbnail(url=f"attachment://{avatar_filename}")
                await interaction.followup.send(embed=embed, file=avatar_file)
            else:
                await interaction.followup.send(embed=embed)

        except aiohttp.ClientError as e:
            await interaction.followup.send(f"❌ MCBansへの接続中にエラーが発生しました: {e}")
        except Exception as e:
            await interaction.followup.send(f"❌ データの解析中に予期せぬエラーが発生しました: {e}")

# --------------------------------------------------------------------------------
# 🔹 Botにコマンドを登録
# --------------------------------------------------------------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(McCommands(bot), guilds=[discord.Object(id=ADMIN_GUILD_ID)])