import discord
from discord import app_commands, Interaction, Object, TextStyle, PermissionOverwrite
from discord.ext import commands, tasks # tasks をインポート
from discord.ui import Modal, TextInput, View, Button, Select

import mysql.connector
import datetime
import io
from minio import Minio

# configから設定値を読み込み
from config import (
    USER_GUILD_ID, ADMIN_GUILD_ID,
    TICKET_CATEGORY_ID,
    MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET_NAME, MINIO_USE_SSL,
    DB_HOST, DB_USER, DB_PASSWORD, DB_NAME
)

# --- DBとMinIOクライアントのセットアップ ---
def get_db_connection():
    return mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME)

minio_client = Minio(
    MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=MINIO_USE_SSL
)

# --- UIコンポーネント ---
# (SurveyFeedbackModal, SurveyView, AssignTicketView, TicketContentModal など、UI関連クラスは変更なしのため省略します)
# (...省略...)
class SurveyFeedbackModal(Modal, title="フィードバック"):
    def __init__(self, ticket_db_id: int): 
        super().__init__()
        self.ticket_db_id = ticket_db_id

        rating = TextInput(
            label="今回の対応の評価（1〜5）",
            placeholder="1〜5の数値でご評価ください（例：5）",
            required=True,
            max_length=1,
            custom_id="rating_input"
        )
        feedback_text = TextInput(
            label="お気づきの点など",
            style=TextStyle.paragraph,
            placeholder="担当者の対応や手続きについて、ご意見・ご感想をお聞かせください。",
            required=False,
            custom_id="feedback_input"
        )
        self.add_item(rating)
        self.add_item(feedback_text)
        self.rating = rating
        self.feedback_text = feedback_text

    async def on_submit(self, interaction: Interaction):
        db_conn = get_db_connection()
        cursor = db_conn.cursor()
        # ratingをint型に変換（入力バリデーションも追加可）
        try:
            rating_value = int(self.rating.value)
        except ValueError:
            await interaction.response.send_message("評価は1〜5の数値で入力してください。", ephemeral=True)
            return
        cursor.execute(
            "UPDATE ticket_surveys SET rating = %s, feedback = %s WHERE ticket_id = %s", 
            (rating_value, self.feedback_text.value, self.ticket_db_id)
        )
        db_conn.commit()
        cursor.close()
        db_conn.close()
        await interaction.response.send_message("貴重なご意見をありがとうございました。", ephemeral=True)
        
class SurveyView(View):
    def __init__(self, ticket_db_id: int, assignee_id: int, target_user_id: int, bot: commands.Bot):
        super().__init__(timeout=None)
        self.ticket_db_id = ticket_db_id
        self.assignee_id = assignee_id
        self.target_user_id = target_user_id
        self.bot = bot

    async def interaction_check(self, interaction: Interaction) -> bool:
        db_conn = get_db_connection()
        cursor = db_conn.cursor()
        cursor.execute("SELECT user_id FROM tickets WHERE id = %s", (self.ticket_db_id,))
        row = cursor.fetchone()
        cursor.close()
        db_conn.close()
        print(f"Interaction check for ticket {self.ticket_db_id}: user_id={row[0] if row else 'None'}, interaction.user.id={interaction.user.id}")
        if row and interaction.user.id == row[0]:
            return True
        await interaction.response.send_message("このアンケートはチケットの起票者のみ回答できます。", ephemeral=True)
        return False

    async def update_survey(self, **kwargs):
        db_conn = get_db_connection()
        cursor = db_conn.cursor()
        cursor.execute("SELECT id FROM ticket_surveys WHERE ticket_id = %s", (self.ticket_db_id,))
        if cursor.fetchone():
            set_clause = ", ".join([f"{key} = %s" for key in kwargs])
            values = list(kwargs.values()) + [self.ticket_db_id]
            cursor.execute(f"UPDATE ticket_surveys SET {set_clause} WHERE ticket_id = %s", values)
        else:
            columns = ", ".join(kwargs.keys())
            placeholders = ", ".join(["%s"] * len(kwargs))
            values = list(kwargs.values())
            sql = f"INSERT INTO ticket_surveys (ticket_id, assignee_id, {columns}) VALUES (%s, %s, {placeholders})"
            values_with_assignee = [self.ticket_db_id, self.assignee_id] + values
            cursor.execute(sql, values_with_assignee)
        db_conn.commit()
        cursor.close()
        db_conn.close()

    @discord.ui.button(label="はい、解決しました", style=discord.ButtonStyle.success, custom_id="survey_resolved_yes")
    async def resolved_yes(self, interaction: Interaction, button: Button):
        await self.update_survey(is_resolved=True)
        # ボタンを無効化
        button.disabled = True
        self.children[1].disabled = True
        await interaction.response.send_modal(SurveyFeedbackModal(self.ticket_db_id))
        followup = await interaction.original_response()
        await followup.edit(view=self)

    @discord.ui.button(label="いいえ、未解決です", style=discord.ButtonStyle.danger, custom_id="survey_resolved_no")
    async def resolved_no(self, interaction: Interaction, button: Button):
        await self.update_survey(is_resolved=False)
        # ボタンを無効化
        button.disabled = True
        self.children[0].disabled = True
        await interaction.response.send_modal(SurveyFeedbackModal(self.ticket_db_id))
        followup = await interaction.original_response()
        await followup.edit(view=self)

# 新規追加: 担当者用クローズ理由入力モーダル
class CloseReasonModal(Modal, title="クローズ理由の入力"):
    reason = TextInput(label="クローズ理由", style=TextStyle.paragraph, placeholder="クローズ理由を入力してください（必須）", required=True, max_length=500)
    def __init__(self, view: "CloseConfirmView"):
        super().__init__()
        self.view = view
    async def on_submit(self, interaction: Interaction):
        # モーダルからクローズ処理を続行
        await self.view.process_close(interaction, self.reason.value)

# ▼▼▼ 修正 ▼▼▼
# クローズ確認用のView
class CloseConfirmView(View):
    def __init__(self, bot: commands.Bot, CaseId: str, user_id: int, assignee_id: int, invoked_by_id: int):
        super().__init__(timeout=60)
        self.bot = bot
        self.CaseId = CaseId
        self.user_id = user_id
        self.assignee_id = assignee_id
        self.invoked_by_id = invoked_by_id
        self._close_reason = None

    @discord.ui.button(label="チケットをクローズ", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: Interaction, button: Button):
        if interaction.user.id != self.invoked_by_id:
            await interaction.response.send_message("この操作はあなたが実行したコマンドからのみ行えます。", ephemeral=True)
            return
        # 担当者の場合はモーダル表示、それ以外は自動クローズ
        if self.invoked_by_id == self.assignee_id:
            # モーダルを表示してクローズ理由を入力させる（最初の応答として）
            await interaction.response.send_modal(CloseReasonModal(self))
        else:
            # 起票者の場合は自動的に理由をセットしてクローズ処理へ
            await interaction.response.defer(thinking=True, ephemeral=False)
            await self.process_close(interaction, "お客様からの申し出による Close")

    async def process_close(self, interaction: Interaction, close_reason: str):
        channel = interaction.channel
        # メッセージ履歴収集（最大1000件）
        messages = []
        async for message in channel.history(limit=1000, oldest_first=True):
            messages.append(message)

        timestamp_str = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")

        attachment_upload_errors = []
        for message in messages:
            for attachment in message.attachments:
                try:
                    file_data = await attachment.read()
                    attachment_path = f"ticket_logs/{self.CaseId}_{timestamp_str}_attachments/{attachment.filename}"
                    minio_client.put_object(
                        MINIO_BUCKET_NAME,
                        attachment_path,
                        data=io.BytesIO(file_data),
                        length=len(file_data),
                        content_type=attachment.content_type or "application/octet-stream"
                    )
                except Exception as e:
                    attachment_upload_errors.append(f"{attachment.filename}: {str(e)}")

        log_lines = []
        for message in messages:
            attachments_info = []
            for att in message.attachments:
                attachments_info.append(f"{att.filename} ({att.url})")
            attachments_text = ", ".join(attachments_info) if attachments_info else ""
            created_at_str = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
            log_lines.append(f"[{created_at_str}] {message.author.display_name}: {message.content} {attachments_text}".strip())

        log_text = "\n".join(log_lines)

        # S3アップロード用ファイル名
        s3_filepath = f"ticket_logs/{self.CaseId}_{timestamp_str}.txt"
        log_bytes = io.BytesIO(log_text.encode("utf-8"))
        try:
            minio_client.put_object(
                MINIO_BUCKET_NAME,
                s3_filepath,
                data=log_bytes,
                length=log_bytes.getbuffer().nbytes,
                content_type="text/plain"
            )
        except Exception as e:
            await interaction.followup.send(f"❌ ログのアップロードに失敗しました: {e}", ephemeral=True)
            return

        # DBのステータスとソリューション、S3パスを更新
        db_conn = get_db_connection()
        cursor = db_conn.cursor()
        close_time = datetime.datetime.now()
        cursor.execute(
            "UPDATE tickets SET status = 'closed', closed_at = %s, solution = %s, s3_filepath = %s WHERE CaseId = %s",
            (close_time, close_reason, s3_filepath, self.CaseId)
        )
        db_conn.commit()
        cursor.execute("SELECT id FROM tickets WHERE CaseId = %s", (self.CaseId,))
        ticket_db_id = cursor.fetchone()[0]
        cursor.close()
        db_conn.close()

        await interaction.response.send_message(
            f"✅ **チケットはクローズされました。**\n<@{self.user_id}>さん、ご協力ありがとうございました。",
            ephemeral=False
        )

        if attachment_upload_errors:
            await channel.send(
                content="⚠️ 一部の添付ファイルのアップロードに失敗しました：\n" + "\n".join(attachment_upload_errors)
            )

        # チャンネル上でアンケートを送信
        survey_view = SurveyView(ticket_db_id=ticket_db_id, assignee_id=self.assignee_id, target_user_id=self.user_id, bot=self.bot)
        await channel.send(
            content=f"<@{self.user_id}>さん、今回のサポートについて簡単なアンケートにご協力ください。",
            view=survey_view
        )

        # チャンネルをリネームし、7日後削除の案内を送信
        await channel.edit(name=f"closed-{channel.name}")
        await channel.send(f"**【ご案内】** このチャンネルは7日後（{ (close_time + datetime.timedelta(days=7)).strftime('%Y年%m月%d日頃')}）に自動的に削除されます。")

        # ボタンを無効化
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

    # (Cancelボタンは変更なし)
    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: Interaction, button: Button):
        if interaction.user.id != self.invoked_by_id:
            await interaction.response.send_message("この操作はあなたが実行したコマンドからのみ行えます。", ephemeral=True)
            return
        await interaction.response.edit_message(content="クローズ処理はキャンセルされました。", view=None)

# --- コマンド本体 ---
async def setup(bot: commands.Bot):
    # (永続ビューの登録は変更なし)
    if not hasattr(bot, '_added_survey_view'):
        bot.add_view(SurveyView(ticket_db_id=None, assignee_id=None, target_user_id=None, bot=bot))
        bot._added_survey_view = True

    # ▼▼▼ 新規追加 ▼▼▼
    # --------------------------------------------------------------------------------
    # 3. 7日経過したクローズ済みチャンネルを削除するバックグラウンドタスク
    # --------------------------------------------------------------------------------
    @tasks.loop(hours=1.0) # 1時間ごとに実行
    async def delete_old_closed_channels():
        try:
            db_conn = get_db_connection()
            cursor = db_conn.cursor(dictionary=True)
            
            # クローズされてから7日以上経過したチケットを取得
            query = "SELECT channel_id, CaseId FROM tickets WHERE status = 'closed' AND closed_at IS NOT NULL AND closed_at < NOW() - INTERVAL 7 DAY"
            cursor.execute(query)
            old_tickets = cursor.fetchall()
            
            if not old_tickets:
                return # 対象がなければ何もしない

            print(f"🧹 古いチケットチャンネルの削除を開始します。対象: {len(old_tickets)}件")
            user_guild = bot.get_guild(USER_GUILD_ID)
            if not user_guild: return

            for ticket in old_tickets:
                try:
                    channel = user_guild.get_channel(ticket['channel_id'])
                    if channel:
                        await channel.delete(reason="クローズ後7日経過したため自動削除")
                        print(f"  - チャンネルを削除しました: {channel.name} (CaseID: {ticket['CaseId']})")

                    # チャンネル削除後、DBのステータスを更新して再処理を防ぐ
                    update_query = "UPDATE tickets SET status = 'archived' WHERE channel_id = %s"
                    cursor.execute(update_query, (ticket['channel_id'],))
                    db_conn.commit()

                except discord.NotFound:
                    # チャンネルが既に見つからない場合でも、ステータスは更新しておく
                    print(f"  - チャンネルが見つかりませんでした (手動削除済みか？): ChannelID {ticket['channel_id']}")
                    update_query = "UPDATE tickets SET status = 'archived' WHERE channel_id = %s"
                    cursor.execute(update_query, (ticket['channel_id'],))
                    db_conn.commit()
                except discord.Forbidden:
                    print(f"  - 権限エラー: チャンネルを削除できませんでした: ChannelID {ticket['channel_id']}")
                except Exception as e:
                    print(f"  - 不明なエラー: {e}")

            cursor.close()
            db_conn.close()

        except Exception as e:
            print(f"❌ チャンネル削除タスクでエラーが発生しました: {e}")

    # Bot起動時にタスクを開始するためのリスナー
    @bot.listen('on_ready')
    async def start_cleanup_task():
        if not delete_old_closed_channels.is_running():
            delete_old_closed_channels.start()

    # (closeコマンド本体は変更なし)
    @app_commands.command(name="close", description="このチケットをクローズします。")
    @app_commands.guilds(Object(id=USER_GUILD_ID))
    async def close(interaction: Interaction):
        # (...コマンドのロジックは省略...)
        channel = interaction.channel
        if not channel.category or channel.category.id != TICKET_CATEGORY_ID:
            await interaction.response.send_message("❌ このコマンドはサポートチケットチャンネル内でのみ使用できます。", ephemeral=True); return
        try:
            CaseId = channel.name.split('-')[0]
            if not (len(CaseId) == 12 and CaseId.isdigit()): raise ValueError
        except (IndexError, ValueError):
            await interaction.response.send_message("❌ このチャンネルは有効なチケットチャンネルではないようです。", ephemeral=True); return
        db_conn = get_db_connection(); cursor = db_conn.cursor(dictionary=True)
        cursor.execute("SELECT user_id, assigned_to FROM tickets WHERE CaseId = %s", (CaseId,)); ticket = cursor.fetchone(); cursor.close(); db_conn.close()
        if not ticket: await interaction.response.send_message("❌ チケット情報がDBに見つかりません。", ephemeral=True); return
        if interaction.user.id not in [ticket['user_id'], ticket['assigned_to']]:
            await interaction.response.send_message("❌ このコマンドは、チケットの起票者または担当者のみ実行できます。", ephemeral=True); return
        view = CloseConfirmView(bot=bot, CaseId=CaseId, user_id=ticket['user_id'], assignee_id=ticket['assigned_to'], invoked_by_id=interaction.user.id)
        await interaction.response.send_message("本当にこのチケットをクローズしますか？この操作は取り消せません。", view=view, ephemeral=True)

    bot.tree.add_command(close)