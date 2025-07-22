from config import ADMIN_GUILD_ID
from discord import app_commands, Interaction, Member, Role
from discord.ext import commands
import discord

ALLOWED_ROLE_NAMES = ["Executive", "Account Mgmt"]

async def setup(bot: commands.Bot):
    @app_commands.command(
        name="add_role",
        description="[管理者サーバー向け] 指定したユーザーに指定のロールを付与します（権限者限定）"
    )
    @app_commands.describe(
        member="ロールを付与するユーザー",
        role="付与したいロール"
    )
    @app_commands.guilds(discord.Object(id=ADMIN_GUILD_ID))
    async def add_role(interaction: Interaction, member: Member, role: Role):
        # 実行者が必要なロールを持っているかチェック
        has_required_role = any(r.name in ALLOWED_ROLE_NAMES for r in interaction.user.roles)
        if not has_required_role:
            await interaction.response.send_message(
                "❌ このコマンドを実行するには `Executive` または `Account Mgmt` ロールが必要です。",
                ephemeral=True
            )
            return

        # Bot のロール階層が付与対象ロールより上である必要がある
        bot_member = interaction.guild.me
        if role.position >= bot_member.top_role.position:
            await interaction.response.send_message(
                f"❌ Botのロール階層が `{role.name}` より下のため、付与できません。",
                ephemeral=True
            )
            return

        # ロールを付与
        try:
            await member.add_roles(role, reason=f"Added by {interaction.user}")
            await interaction.response.send_message(
                f"✅ {member.mention} に {role.mention} を付与しました。",
                ephemeral=False
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ 権限が不足しているためロールを付与できません。",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ エラーが発生しました: {e}",
                ephemeral=True
            )

    bot.tree.add_command(add_role)