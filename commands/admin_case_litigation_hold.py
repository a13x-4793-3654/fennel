import discord
from discord import app_commands, Interaction
from discord.ext import commands
from config import *
import mysql.connector
from minio import Minio
from minio.error import S3Error
import io
import zipfile
def get_db_connection():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )

# MinIO クライアントの初期化（configから取得推奨）
minio_client = Minio(
    MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=MINIO_USE_SSL
)

class AdminCaseLog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="get_case_log", description="指定されたケースのログと添付ファイルを取得します")
    @app_commands.describe(case_id="対象のケースID（12桁）")
    @app_commands.guilds(discord.Object(id=ADMIN_GUILD_ID))
    async def get_case_log(self, interaction: Interaction, case_id: str):
        # チャンネル制限チェック
        if interaction.channel.id != LEGAL_RESPONSE_CHANNEL_ID:
            await interaction.response.send_message("このコマンドは法的対応チャンネル内でのみ実行できます。", ephemeral=True)
            return

        # DB から s3_filepath を取得
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM tickets WHERE CaseId = %s", (case_id,))
        ticket = cursor.fetchone()
        cursor.close()
        conn.close()

        if not ticket:
            await interaction.response.send_message("該当するチケットが見つかりません。", ephemeral=True)
            return

        s3_filepath = ticket.get("s3_filepath")
        if not s3_filepath:
            await interaction.response.send_message("ログファイルの情報がありません。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            log_obj = minio_client.get_object(MINIO_BUCKET_NAME, s3_filepath)
            log_data = log_obj.read()
            log_file = discord.File(io.BytesIO(log_data), filename="case_log.txt")
        except S3Error as e:
            await interaction.followup.send(f"ログファイルの取得に失敗しました: {str(e)}", ephemeral=True)
            return

        # 添付ファイルをすべて収集
        attached_files = []
        try:
            objects = minio_client.list_objects(MINIO_BUCKET_NAME, prefix=f"ticket_logs/{case_id}_", recursive=True)
            for obj in objects:
                if obj.object_name == s3_filepath:
                    continue  # ログファイル自身は除外
                data = minio_client.get_object(MINIO_BUCKET_NAME, obj.object_name).read()
                filename = obj.object_name.split("/")[-1]
                attached_files.append((filename, io.BytesIO(data)))
        except Exception as e:
            await interaction.followup.send(f"S3から添付ファイルを取得中にエラーが発生しました: {e}", ephemeral=True)
            return

        # 追加情報を収集
        try:
            user = await self.bot.fetch_user(ticket["user_id"])
        except:
            user = None
        try:
            assignee = await self.bot.fetch_user(ticket["assigned_to"]) if ticket["assigned_to"] else None
        except:
            assignee = None

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM ticket_surveys WHERE ticket_id = %s", (ticket["id"],))
        survey = cursor.fetchone()
        cursor.close()
        conn.close()

        # 添付ファイルの有無判定（s3_filepath以外があるか）
        has_attachments = any(attached_files)

        summary = (
            "*** 訴訟ホールド情報 ***\n"
            "***     Internal Use    ***\n\n"
            "🔸 基本情報\n"
            f"ケース ID : {ticket['CaseId']}\n"
            f"起票者 : {user.name if user else ticket['user_id']}\n"
            f"対応者 : {assignee.name if assignee else ticket['assigned_to']}\n"
            f"起票カテゴリ : {ticket['category']}\n"
            f"起票内容 : {ticket['content']}\n"
            f"状況 : {ticket['status']}\n"
            f"解決方法 : {ticket.get('solution') or '未入力'}\n"
            f"エスカレーションの有無 : {'あり' if ticket.get('is_escalated') else 'なし'}\n"
            f"作成日時 : {ticket['created_at']}\n"
            f"クローズ日時 : {ticket.get('closed_at') or '未クローズ'}\n\n"
            "🔸 Survey 情報\n"
            f"Resolved : {'はい' if survey and survey['is_resolved'] == 1 else 'いいえ' if survey and survey['is_resolved'] == 0 else 'N/A'}\n"
            f"CSAT : {survey['rating'] if survey else 'N/A'}\n"
            f"Verbatim: {survey['feedback'] if survey and survey['feedback'] else 'N/A'}\n\n"
            "🔸 添付ファイルの有無\n"
            f"{'あり' if has_attachments else 'なし'}"
        )

        # Discord の送信制限に応じて送信形式を分岐
        total_size = sum(buf.getbuffer().nbytes for _, buf in attached_files) + len(log_data)
        if total_size < 8 * 1024 * 1024:  # Discord ファイル制限
            files = [log_file]
            for name, buf in attached_files:
                buf.seek(0)
                files.append(discord.File(buf, filename=name))
            await interaction.followup.send(content=summary, files=files)
        else:
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("case_log.txt", log_data)
                for name, buf in attached_files:
                    buf.seek(0)
                    z.writestr(name, buf.read())
            zip_buffer.seek(0)
            await interaction.followup.send(
                content=summary + "\n\n※ファイルは zip 圧縮されています。",
                file=discord.File(zip_buffer, filename=f"{case_id}_case_data.zip")
            )

async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCaseLog(bot))