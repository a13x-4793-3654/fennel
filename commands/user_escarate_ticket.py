import discord
from discord import app_commands, Interaction, Object, TextStyle
from discord.ext import commands, tasks
from discord.ui import Modal, TextInput
from discord.utils import get
from datetime import datetime, timedelta
from config import *
import mysql.connector

def get_db_connection():
    return mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME)

class EscalationReasonModal(Modal, title="エスカレーション理由の入力"):
    def __init__(self, case_id, category, content, assignee_id, bot):
        super().__init__()
        self.case_id = case_id
        self.category = category
        self.content = content
        self.assignee_id = assignee_id
        self.bot = bot

        self.reason = TextInput(
            label="理由",
            style=TextStyle.paragraph,
            placeholder="なぜこのケースをエスカレーションするのか説明してください。",
            required=True,
            max_length=1000,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE tickets SET is_escalated = 1 WHERE CaseId = %s", (self.case_id,))
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            await interaction.followup.send(f"DB更新中にエラーが発生しました: {e}", ephemeral=True)
            return

        admin_guild = self.bot.get_guild(ADMIN_GUILD_ID)
        notify_channel = admin_guild.get_channel(ESCALATION_NOTIFICATION_CHANNEL_ID)
        if not notify_channel:
            await interaction.followup.send("エスカレーション通知チャンネルが見つかりません。", ephemeral=True)
            return

        embed = discord.Embed(title="🚨 エスカレーション通知", color=discord.Color.red())
        embed.add_field(name="ケース ID", value=self.case_id, inline=False)
        embed.add_field(name="カテゴリ", value=self.category, inline=False)
        embed.add_field(name="問い合わせ内容", value=self.content, inline=False)
        embed.add_field(name="現在の対応者", value=f"<@{self.assignee_id}>", inline=False)
        embed.add_field(name="理由", value=self.reason.value, inline=False)

        message = await notify_channel.send(embed=embed)
        await message.add_reaction("📌")

        await interaction.followup.send("エスカレーション通知を送信しました。", ephemeral=True)


class Escalation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cleanup_escalated_channels.start()

    @app_commands.command(name="escalate", description="チケットを管理者にエスカレーションします。")
    @app_commands.guilds(Object(id=USER_GUILD_ID))
    async def escalate(self, interaction: Interaction):
        if not interaction.channel or not interaction.channel.name or "-" not in interaction.channel.name:
            await interaction.response.send_message("このコマンドはチケットチャンネル内でのみ使用できます。", ephemeral=True)
            return

        case_id = interaction.channel.name.split("-")[0]
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM tickets WHERE CaseId = %s", (case_id,))
        ticket = cursor.fetchone()
        cursor.close()
        conn.close()

        if not ticket:
            await interaction.response.send_message("チケット情報が見つかりません。", ephemeral=True)
            return

        if interaction.user.id != ticket["assigned_to"]:
            await interaction.response.send_message("このチケットの対応者のみがエスカレーション可能です。", ephemeral=True)
            return

        await interaction.response.send_modal(EscalationReasonModal(
            case_id=case_id,
            category=ticket["category"],
            content=ticket["content"],
            assignee_id=ticket["assigned_to"],
            bot=self.bot
        ))

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return
        if reaction.message.channel.guild.id != ADMIN_GUILD_ID:
            return
        if str(reaction.emoji) != "📌":
            return
        if reaction.message.embeds:
            embed = reaction.message.embeds[0]
            if embed.title == "🚨 エスカレーション通知":
                case_id_field = discord.utils.get(embed.fields, name="ケース ID")
                if case_id_field:
                    case_id = case_id_field.value
                    guild = reaction.message.guild
                    existing = discord.utils.get(guild.text_channels, name=f"対応相談-{case_id}")
                    if existing:
                        return
                    category = guild.get_channel(ESCALATION_CATEGORY_ID)
                    overwrites = {
                        guild.default_role: discord.PermissionOverwrite(read_messages=False),
                        user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                    }
                    await guild.create_text_channel(
                        name=f"対応相談-{case_id}",
                        category=category,
                        overwrites=overwrites,
                        reason="エスカレーションに伴う対応相談"
                    )

    @tasks.loop(hours=1)
    async def cleanup_escalated_channels(self):
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT CaseId, channel_id, closed_at FROM tickets WHERE is_escalated = 1 AND status IN ('closed', 'archived') AND closed_at < NOW() - INTERVAL 7 DAY"
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        admin_guild = self.bot.get_guild(ADMIN_GUILD_ID)
        if not admin_guild:
            return

        for row in rows:
            ch_name = f"対応相談-{row['CaseId']}"
            ch = discord.utils.get(admin_guild.text_channels, name=ch_name)
            if ch:
                await ch.delete(reason="クローズ後7日経過したため自動削除")

    @cleanup_escalated_channels.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Escalation(bot))