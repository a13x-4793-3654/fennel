# commands/create_ticket.py (DBカラム名修正版)

# 必要なライブラリをインポート
import discord
from discord import app_commands, Interaction, Member, Role, SelectOption, TextStyle, Object, PermissionOverwrite, CategoryChannel
from discord.ext import commands
from discord.ui import Modal, TextInput, View, Select
import mysql.connector
import datetime
import random

# configから設定値を読み込み
from config import (
    USER_GUILD_ID, ADMIN_GUILD_ID, ADMIN_NOTIFY_CHANNEL_ID,
    TICKET_CATEGORY_ID, STAFF_ROLE_ID, DB_HOST, DB_USER, DB_PASSWORD, DB_NAME
)

def get_db_connection():
    return mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME)

# 担当者アサインの共通処理
async def handle_ticket_assignment(message: discord.Message, staff_member: discord.Member, bot: commands.Bot):
    if not message.embeds: return
    
    embed = message.embeds[0]
    if "Ticket ID:" not in embed.footer.text: return

    try:
        ticket_db_id = int(embed.footer.text.split(":")[-1].strip())
        
        # ▼▼▼ 修正 ▼▼▼
        # 担当者IDとステータスをDBに保存
        db_conn = get_db_connection(); cursor = db_conn.cursor()
        sql = "UPDATE tickets SET assigned_to = %s, status = 'assigned' WHERE CaseId = %s"
        # ▲▲▲ 修正 ▲▲▲
        val = (staff_member.id, ticket_db_id)
        cursor.execute(sql, val)
        db_conn.commit()
        
        cursor.execute("SELECT channel_id FROM tickets WHERE CaseId = %s", (ticket_db_id,)); result = cursor.fetchone()
        cursor.close(); db_conn.close()

        if not result: return
        channel_id = result[0]
        
        user_guild = bot.get_guild(USER_GUILD_ID)
        if not user_guild: return

        ticket_channel = user_guild.get_channel(channel_id)
        if not ticket_channel:
            try:
                ticket_channel = await user_guild.fetch_channel(channel_id)
            except discord.NotFound:
                return

        await ticket_channel.set_permissions(staff_member, read_messages=True, send_messages=True)
        assign_msg = f"✋ {staff_member.mention} が担当者としてアサインされました。"
        await ticket_channel.send(assign_msg)
        await message.reply(assign_msg)
        await message.clear_reactions()
        await message.edit(content="**このチケットは担当者がアサインされました。**", view=None)
    except Exception as e:
        print(f"Error during ticket assignment: {e}")

# (AssignTicketViewクラスは変更なし)
class AssignTicketView(View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot
    @discord.ui.button(label="担当する", style=discord.ButtonStyle.success, emoji="✋", custom_id="assign_ticket_button")
    async def assign_button(self, interaction: Interaction, button: discord.ui.Button):
        await handle_ticket_assignment(interaction.message, interaction.user, self.bot)
        await interaction.response.defer()

# 問い合わせ内容を入力させるモーダル
class TicketContentModal(Modal):
    def __init__(self, category: str):
        super().__init__(title=f"{category} について")
        self.category = category
        self.content = TextInput(label="お問い合わせ内容", style=TextStyle.paragraph, placeholder="できるだけ詳しく内容を記述してください。", required=True, max_length=1500)
        self.add_item(self.content)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            db_conn = get_db_connection()
            cursor = db_conn.cursor()
            
            # 1. 重複しないCaseIDを生成
            while True:
                now = datetime.datetime.now()
                case_id_variable = f"{now.strftime('%y%m%d')}{random.randint(100000, 999999)}"
                # ▼▼▼ 修正 ▼▼▼
                cursor.execute("SELECT id FROM tickets WHERE CaseId = %s", (case_id_variable,))
                # ▲▲▲ 修正 ▲▲▲
                if not cursor.fetchone():
                    break
            
            # 2. データベースにチケット情報を保存
            # ▼▼▼ 修正 ▼▼▼
            sql = "INSERT INTO tickets (user_id, guild_id, category, content, CaseId) VALUES (%s, %s, %s, %s, %s)"
            # ▲▲▲ 修正 ▲▲▲
            val = (interaction.user.id, interaction.guild.id, self.category, self.content.value, case_id_variable)
            cursor.execute(sql, val)
            db_conn.commit()
            
            ticket_db_id = cursor.lastrowid

            # 3. プライベートチャンネルを作成
            parent_category = interaction.guild.get_channel(TICKET_CATEGORY_ID)
            if not parent_category or not isinstance(parent_category, CategoryChannel):
                await interaction.followup.send("❌ エラー: チケット用の親カテゴリが見つかりません。", ephemeral=True); return
            
            overwrites = {
                interaction.guild.default_role: PermissionOverwrite(read_messages=False),
                interaction.user: PermissionOverwrite(read_messages=True, send_messages=True),
                interaction.guild.me: PermissionOverwrite(read_messages=True, send_messages=True)
            }
            
            channel_name = f"{case_id_variable}-{self.category}"
            ticket_channel = await interaction.guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                category=parent_category,
                topic=f"Ticket for {interaction.user} (ID: {interaction.user.id}) | Case ID: {case_id_variable}"
            )
            
            cursor.execute("UPDATE tickets SET channel_id = %s WHERE id = %s", (ticket_channel.id, ticket_db_id))
            db_conn.commit()
            cursor.close()
            db_conn.close()

            # 4. 管理者サーバーに通知を送信
            admin_guild = interaction.client.get_guild(ADMIN_GUILD_ID)
            notify_channel = admin_guild.get_channel(ADMIN_NOTIFY_CHANNEL_ID)
            
            embed = discord.Embed(title="🎫 新規チケットが作成されました", description=f"**Case ID:** `{case_id_variable}`\n**カテゴリ:** {self.category}", color=discord.Color.blue(), timestamp=datetime.datetime.now())
            embed.add_field(name="作成者", value=interaction.user.mention, inline=True)
            embed.add_field(name="チケットチャンネル", value=ticket_channel.mention, inline=True)
            embed.add_field(name="内容", value=f"```\n{self.content.value[:1000]}\n```", inline=False)
            embed.set_footer(text=f"Ticket ID: {case_id_variable}")

            await notify_channel.send(content="新しい問い合わせです。担当者はボタンまたは「✋」のリアクションでアサインしてください。", embed=embed, view=AssignTicketView(bot=interaction.client))

            # 5. ユーザーへの完了通知
            user_embed = discord.Embed(title="✅ お問い合わせを受け付けました", description=f"担当者が確認するまでしばらくお待ちください。\nやり取りはこちらのチャンネルで行います: {ticket_channel.mention}", color=discord.Color.green())
            user_embed.add_field(name="受付番号 (Case ID)", value=case_id_variable, inline=True)
            user_embed.add_field(name="カテゴリ", value=self.category, inline=True)
            await interaction.followup.send(embed=user_embed, ephemeral=True)
            await ticket_channel.send(f"{interaction.user.mention} お問い合わせありがとうございます。\n担当者がアサインされるまでしばらくお待ちください。")
            await ticket_channel.send(
                content=(
                    f"📄 **お問い合わせ内容**\n"
                    f"カテゴリ: `{self.category}`\n"
                    f"```\n{self.content.value}\n```"
                )
            )
            
        except Exception as e:
            print(f"An error occurred in TicketContentModal: {e}"); await interaction.followup.send(f"❌ エラーが発生しました。管理者に連絡してください。\n`{e}`", ephemeral=True)

# (ViolationConfirmView, TicketCategorySelect, TicketCreationView は変更なし)
class ViolationConfirmView(View):
    def __init__(self): super().__init__(timeout=180)
    @discord.ui.button(label="同意して続ける", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: Interaction, button: discord.ui.Button): await interaction.response.send_modal(TicketContentModal(category="違反報告"))
    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: Interaction, button: discord.ui.Button): await interaction.response.edit_message(content="操作はキャンセルされました。", view=None)
class TicketCategorySelect(Select):
    def __init__(self):
        options = [SelectOption(label="要望・提案",emoji="💡"),SelectOption(label="不具合報告",emoji="🐛"),SelectOption(label="違反報告",emoji="🚨"),SelectOption(label="各種申請",emoji="📝"),SelectOption(label="その他",emoji="🤔")]
        super().__init__(placeholder="問い合わせカテゴリを選択してください...", options=options, custom_id="ticket_category_select")
    async def callback(self, interaction: Interaction):
        selected_category = self.values[0]
        if selected_category == "違反報告":
            embed = discord.Embed(title="🚨 違反報告に関する注意事項", description="- 虚偽の報告は行わないでください。\n- 証拠となるスクリーンショットやメッセージリンクを準備してください。\n- 報告内容の守秘義務を遵守してください。", color=discord.Color.orange())
            await interaction.response.send_message(embed=embed, view=ViolationConfirmView(), ephemeral=True)
        else: await interaction.response.send_modal(TicketContentModal(category=selected_category))
class TicketCreationView(View):
    def __init__(self): super().__init__(timeout=None); self.add_item(TicketCategorySelect())

# (setup関数とリアクションリスナーは変更なし)
async def setup(bot: commands.Bot):
    bot.add_view(TicketCreationView())
    bot.add_view(AssignTicketView(bot))
    @app_commands.command(name="create_ticket", description="サポートへのお問い合わせチケットを作成します。")
    @app_commands.guilds(Object(id=USER_GUILD_ID))
    async def create_ticket(interaction: Interaction):
        await interaction.response.send_message("お問い合わせの種類を選択してください。", view=TicketCreationView(), ephemeral=True)
    listener_name = 'on_ticket_assign'
    if not any(listener.__name__ == listener_name for listener in bot.extra_events.get('on_raw_reaction_add', [])):
        @bot.listen('on_raw_reaction_add')
        async def on_ticket_assign(payload: discord.RawReactionActionEvent):
            if payload.user_id == bot.user.id: return
            if payload.channel_id != ADMIN_NOTIFY_CHANNEL_ID or str(payload.emoji) != "✋": return
            guild = bot.get_guild(payload.guild_id);
            if not guild: return
            member = guild.get_member(payload.user_id)
            if not member: return
            channel = guild.get_channel(payload.channel_id)
            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.NotFound: return
            await handle_ticket_assignment(message, member, bot)
    bot.tree.add_command(create_ticket)