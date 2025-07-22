# commands/autorole.py (機能追加・修正版)

import discord
from discord import app_commands, Object, Interaction
from discord.ext import commands
import mysql.connector
from typing import Optional

# config.py から必要な設定値を読み込む
from config import (
    USER_GUILD_ID,
    ADMIN_GUILD_ID,
    USER_ROLE_ID,
    STAFF_ROLE_ID,
    ESCALATE_ROLE_ID,
    DB_HOST,
    DB_USER,
    DB_PASSWORD,
    DB_NAME
)

# データベース接続を管理する関数
def get_db_connection():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )

# --------------------------------------------------------------------------------
# 1. ロールチェックと付与の共通ロジック
# --------------------------------------------------------------------------------
async def check_and_assign_roles(member: discord.Member) -> str:
    """
    指定されたメンバーのロールをDBと照合し、必要に応じて付与・削除する共通関数。
    戻り値として処理内容の文字列を返す。
    """
    if member.bot:
        return "対象外 (Bot)"

    guild = member.guild
    current_role_ids = {role.id for role in member.roles}
    
    # --- スタッフかどうかのDBチェック ---
    try:
        db_conn = get_db_connection()
        cursor = db_conn.cursor()
        query = "SELECT Is_EscalateEng FROM SupportUsers WHERE UserId = %s"
        cursor.execute(query, (member.id,))
        result = cursor.fetchone()
        cursor.close()
        db_conn.close()

        # --- DBにレコードが存在した場合 (スタッフ) ---
        if result:
            is_escalate_eng = result[0]
            
            staff_role = guild.get_role(STAFF_ROLE_ID)
            staff_lead_role = guild.get_role(ESCALATE_ROLE_ID)
            welcome_role = guild.get_role(USER_ROLE_ID)

            roles_to_add = []
            roles_to_remove = []

            if staff_role and staff_role.id not in current_role_ids:
                roles_to_add.append(staff_role)
            
            if is_escalate_eng and staff_lead_role and staff_lead_role.id not in current_role_ids:
                roles_to_add.append(staff_lead_role)
            elif not is_escalate_eng and staff_lead_role and staff_lead_role.id in current_role_ids:
                roles_to_remove.append(staff_lead_role)
            
            if welcome_role and welcome_role.id in current_role_ids:
                roles_to_remove.append(welcome_role)

            if roles_to_add:
                await member.add_roles(*roles_to_add, reason="役割の自動同期")
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="役割の自動同期")
            
            if roles_to_add or roles_to_remove:
                return f"スタッフとして同期: {member.display_name}"
            return "変更なし (スタッフ)"

        # --- DBにレコードが存在しなかった場合 (一般ユーザー) ---
        else:
            welcome_role = guild.get_role(USER_ROLE_ID)
            staff_role = guild.get_role(STAFF_ROLE_ID)
            staff_lead_role = guild.get_role(ESCALATE_ROLE_ID)
            
            roles_to_add = []
            roles_to_remove = []

            if welcome_role and welcome_role.id not in current_role_ids:
                roles_to_add.append(welcome_role)

            if staff_role and staff_role.id in current_role_ids:
                roles_to_remove.append(staff_role)
            if staff_lead_role and staff_lead_role.id in current_role_ids:
                roles_to_remove.append(staff_lead_role)

            if roles_to_add:
                await member.add_roles(*roles_to_add, reason="役割の自動同期")
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="役割の自動同期")

            if roles_to_add or roles_to_remove:
                return f"一般ユーザーとして同期: {member.display_name}"
            return "変更なし (一般)"

    except mysql.connector.Error as err:
        return f"DBエラー: {err}"
    except discord.Forbidden:
        return f"権限エラー: {member.display_name} のロールを操作できません"
    except Exception as e:
        return f"不明なエラー: {e}"


# --------------------------------------------------------------------------------
# 2. 各タイミングで共通ロジックを呼び出す
# --------------------------------------------------------------------------------
async def setup(bot: commands.Bot):
    
    # --- Bot起動時に一度だけ実行するリスナー ---
    @bot.listen('on_ready')
    async def on_ready_role_check():
        if not hasattr(bot, '_startup_role_check_done'):
            bot._startup_role_check_done = True
            
            print("▶️ Bot起動時の全メンバーロールチェックを開始します...")
            guild = bot.get_guild(USER_GUILD_ID)
            if not guild:
                print(f"❌ ギルド {USER_GUILD_ID} が見つかりません。")
                return

            print(f"✅ ギルド '{guild.name}' の {len(guild.members)} 人のメンバーをチェックします。")
            for member in guild.members:
                await check_and_assign_roles(member)

            print("✅ Bot起動時の全メンバーロールチェックが完了しました。")

    # --- ユーザー参加時に実行するリスナー ---
    @bot.listen('on_member_join')
    async def on_member_join_role_check(member: discord.Member):
        if member.guild.id == USER_GUILD_ID:
            status = await check_and_assign_roles(member)
            print(f"👤 ユーザー参加: {status}")

    # --- 管理者向けの手動実行コマンド ---
    @app_commands.command(name="role_check", description="[管理者向け] メンバーのロールをDBと同期します。")
    @app_commands.guilds(Object(id=ADMIN_GUILD_ID))
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(target="対象ユーザー（指定しない場合は全ユーザー）")
    async def role_check(interaction: Interaction, target: Optional[discord.Member] = None):
        
        user_guild = bot.get_guild(USER_GUILD_ID)
        if not user_guild:
            await interaction.response.send_message(f"❌ ユーザーギルド (ID: {USER_GUILD_ID}) が見つかりません。", ephemeral=True)
            return
        
        # 特定のユーザーを対象とする場合
        if target:
            await interaction.response.defer(ephemeral=True)
            # 🔽 ここから修正 🔽
            # 管理者サーバーのメンバー(target)ではなく、同じIDを持つユーザーサーバーのメンバーを取得
            member_in_user_guild = user_guild.get_member(target.id)
            if not member_in_user_guild:
                await interaction.followup.send(f"❌ ユーザー '{target.display_name}' はユーザーサーバーに参加していません。", ephemeral=True)
                return
            
            status = await check_and_assign_roles(member_in_user_guild)
            # 🔼 ここまで修正 🔼
            await interaction.followup.send(f"ユーザー '{member_in_user_guild.display_name}' のチェックが完了しました。\n結果: `{status}`", ephemeral=True)
        
        # 全ユーザーを対象とする場合
        else:
            await interaction.response.defer(ephemeral=True, thinking=True)
            print("▶️ /role_check コマンドによる全メンバーのロールチェックを開始します...")
            
            updated_count = 0
            for member in user_guild.members:
                status = await check_and_assign_roles(member)
                if "同期" in status:
                    updated_count += 1
            
            await interaction.followup.send(f"✅ 全メンバーのロールチェックが完了しました。\n`{updated_count}` 人のメンバーのロールが更新されました。", ephemeral=True)
            print("✅ /role_check コマンドによる全メンバーのロールチェックが完了しました。")

    bot.tree.add_command(role_check)