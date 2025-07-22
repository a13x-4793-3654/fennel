import discord
import mysql.connector
from discord import app_commands, Interaction
from discord.ext import commands
from discord.ext import tasks
import datetime
from discord.ui import Modal, TextInput, Button, View
from discord import TextStyle, Object
from config import *

def get_db_connection():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )

class PrivChannelRequestModal(Modal, title="プライベートチャンネル申請"):
    def __init__(self, bot: commands.Bot, user: discord.User):
        super().__init__()
        self.bot = bot
        self.requester = user

        self.channel_title = TextInput(
            label="チャネルタイトル",
            style=TextStyle.short,
            max_length=100,
            required=True,
        )
        self.channel_content = TextInput(
            label="チャネル説明・用途",
            style=TextStyle.paragraph,
            max_length=1000,
            required=True,
        )

        self.add_item(self.channel_title)
        self.add_item(self.channel_content)

    async def on_submit(self, interaction: Interaction):
        # Insert request into DB
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO PrivateChannel (channel_name, channle_description, status_code, requestor) VALUES (%s, %s, %s, %s)",
            (self.channel_title.value, self.channel_content.value, 0, self.requester.id)
        )
        conn.commit()
        cursor.close()
        conn.close()

        admin_guild = self.bot.get_guild(ADMIN_GUILD_ID)
        category = admin_guild.get_channel(ADMIN_REQUEST_CATEGORY_ID)

        overwrites = {
            admin_guild.default_role: discord.PermissionOverwrite(read_messages=True),
        }

        channel = await admin_guild.create_text_channel(
            name="チャネル作成承認_Private",
            overwrites=overwrites,
            category=category,
            reason="プライベートチャネル申請"
        )

        embed = discord.Embed(
            title="📥 プライベートチャンネル作成申請",
            description=f"申請者: {self.requester.mention}",
            color=discord.Color.blue()
        )
        embed.add_field(name="タイトル", value=self.channel_title.value, inline=False)
        embed.add_field(name="説明", value=self.channel_content.value, inline=False)

        view = PrivChannelApprovalView(
            bot=self.bot,
            title=self.channel_title.value,
            content=self.channel_content.value,
            requester=self.requester,
            channel_request=channel
        )

        await channel.send(embed=embed, view=view)
        await interaction.response.send_message("✅ 申請が送信されました。管理者の承認をお待ちください。", ephemeral=True)


class PrivChannelApprovalView(View):
    def __init__(self, bot: commands.Bot, title: str, content: str, requester: discord.User, channel_request: discord.TextChannel, is_extension: bool = False):
        super().__init__(timeout=None)
        self.bot = bot
        self.title = title
        self.content = content
        self.requester = requester
        self.channel_request = channel_request
        self.is_extension = is_extension

    @discord.ui.button(label="承認", style=discord.ButtonStyle.success)
    async def approve(self, interaction: Interaction, button: Button):
        if self.is_extension:
            # 延長処理：チャネル作成せず、終了日を延長
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE PrivateChannel SET status_code=%s, approve_date=NOW(), close_date=DATE_ADD(NOW(), INTERVAL 35 DAY), approver=%s, extend_count = extend_count + 1 WHERE channel_name=%s AND requestor=%s ORDER BY id DESC LIMIT 1",
                (1, interaction.user.id, self.title, self.requester.id)
            )
            conn.commit()
            cursor.close()
            conn.close()
            valid_until = (datetime.datetime.now() + datetime.timedelta(days=30)).strftime("%Y-%m-%d")
            await interaction.response.send_message(f"✅ 承認され、有効期限が延長されました。有効期限は {valid_until} までです。", ephemeral=True)
            await self.channel_request.delete()
            return

        user_guild = self.bot.get_guild(USER_GUILD_ID)
        category = user_guild.get_channel(USER_PRIVATE_CATEGORY_ID)
        overwrites = {
            user_guild.default_role: discord.PermissionOverwrite(read_messages=False),
            self.requester: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            user_guild.me: discord.PermissionOverwrite(read_messages=True)
        }

        new_channel = await user_guild.create_text_channel(
            name=self.title,
            topic=self.content,
            overwrites=overwrites,
            category=category,
            reason="申請に基づくプライベートチャンネル作成"
        )

        # Update DB record for approval
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE PrivateChannel SET status_code=%s, approve_date=NOW(), close_date=DATE_ADD(NOW(), INTERVAL 35 DAY), approver=%s WHERE channel_name=%s AND requestor=%s ORDER BY id DESC LIMIT 1",
            (1, interaction.user.id, self.title, self.requester.id)
        )
        conn.commit()
        cursor.close()
        conn.close()

        valid_until = (datetime.datetime.now() + datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        await interaction.response.send_message("✅ 承認され、チャンネルを作成しました。", ephemeral=True)
        await new_channel.send(f"{self.requester.mention} チャンネルが作成されました。ご利用ください。有効期限は {valid_until} までです。")
        await self.channel_request.delete()

    @discord.ui.button(label="非承認", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: Interaction, button: Button):
        try:
            await self.requester.send("❌ 申請されたプライベートチャンネルの作成は非承認となりました。")
        except discord.Forbidden:
            pass  # DMできない場合は無視
        await interaction.response.send_message("🚫 非承認として処理しました。", ephemeral=True)
        await self.channel_request.delete()


class PrivateChannelCog(commands.Cog):
    def cog_load(self):
        self.cleanup_expired_channels.start()

    @tasks.loop(hours=24)
    async def cleanup_expired_channels(self):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT channel_name FROM PrivateChannel WHERE close_date < NOW() AND status_code = 1"
        )
        expired_channels = cursor.fetchall()
        cursor.close()
        conn.close()

        if not expired_channels:
            return

        guild = self.bot.get_guild(USER_GUILD_ID)
        for (channel_name,) in expired_channels:
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if channel:
                try:
                    await channel.delete(reason="自動削除: 有効期限切れ")
                except discord.HTTPException:
                    pass

    def cog_unload(self):
        self.cleanup_expired_channels.cancel()

    @app_commands.command(name="extend", description="プライベートチャネルの継続利用申請を行います")
    @app_commands.guilds(Object(id=USER_GUILD_ID))
    async def extend_priv_channel(self, interaction: Interaction):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, close_date, extend_count FROM PrivateChannel WHERE channel_name=%s AND requestor=%s ORDER BY id DESC LIMIT 1",
            (interaction.channel.name, interaction.user.id)
        )
        row = cursor.fetchone()
        if not row:
            await interaction.response.send_message("❌ 申請記録が見つかりません。", ephemeral=True)
            cursor.close()
            conn.close()
            return

        request_id, close_date, extend_count = row
        if (close_date - datetime.datetime.now()).days > 20:
            await interaction.response.send_message("❌ 有効期限の15日前以降でないと延長申請はできません。", ephemeral=True)
            cursor.close()
            conn.close()
            return

        cursor.execute(
            "INSERT INTO PrivateChannel (channel_name, channle_description, status_code, requestor) "
            "SELECT channel_name, channle_description, 0, requestor FROM PrivateChannel WHERE id=%s",
            (request_id,)
        )
        conn.commit()
        cursor.close()
        conn.close()

        admin_guild = self.bot.get_guild(ADMIN_GUILD_ID)
        category = admin_guild.get_channel(ADMIN_REQUEST_CATEGORY_ID)
        overwrites = {
            admin_guild.default_role: discord.PermissionOverwrite(read_messages=False),
            admin_guild.me: discord.PermissionOverwrite(read_messages=True),
        }
        channel = await admin_guild.create_text_channel(
            name="チャネル延長申請_Private",
            overwrites=overwrites,
            category=category,
            reason="プライベートチャネル延長申請"
        )

        embed = discord.Embed(
            title="🔁 プライベートチャンネル延長申請",
            description=f"申請者: {interaction.user.mention}",
            color=discord.Color.orange()
        )
        embed.add_field(name="チャネル", value=interaction.channel.name, inline=False)
        embed.add_field(name="現在の終了日", value=str(close_date), inline=False)
        view = PrivChannelApprovalView(
            bot=self.bot,
            title=interaction.channel.name,
            content="延長申請",
            requester=interaction.user,
            channel_request=channel,
            is_extension=True
        )
        await channel.send(embed=embed, view=view)
        await interaction.response.send_message("✅ 延長申請が送信されました。", ephemeral=True)
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="create_priv_channel", description="プライベートチャネルの作成を申請します")
    @app_commands.guilds(Object(id=USER_GUILD_ID))
    async def create_priv_channel(self, interaction: Interaction):
        await interaction.response.send_modal(PrivChannelRequestModal(self.bot, interaction.user))


async def setup(bot):
    cog = PrivateChannelCog(bot)
    await bot.add_cog(cog)
    # Add extend_priv_channel command to the tree
    #cog.bot.tree.add_command(cog.extend_priv_channel)