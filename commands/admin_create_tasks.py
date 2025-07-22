from config import ADMIN_GUILD_ID
from discord import app_commands, Interaction, Role, TextChannel, PermissionOverwrite
from discord.ext import commands
from discord import ui
import discord

class TaskNameModal(ui.Modal, title="タスク名を入力してください"):
    task_name = ui.TextInput(label="タスク名", placeholder="例: urgent-fix", max_length=32)

    def __init__(self, bot: commands.Bot, interaction: Interaction):
        super().__init__()
        self.bot = bot
        self.interaction = interaction

    async def on_submit(self, interaction: Interaction):
        task_name = self.task_name.value
        guild = interaction.guild

        # 次にロールを選択させるセレクトメニューを送信
        roles = [role for role in guild.roles if role != guild.default_role]

        # 選択肢の上限は25
        options = [
            discord.SelectOption(label=role.name, value=str(role.id)) for role in roles[:25]
        ]

        class RoleSelect(ui.Select):
            def __init__(self):
                super().__init__(placeholder="ロールを選択してください", options=options)

            async def callback(self, select_interaction: Interaction):
                role_id = int(self.values[0])
                role = discord.utils.get(guild.roles, id=role_id)

                # チャンネル名の重複確認
                if discord.utils.get(guild.text_channels, name=task_name):
                    await select_interaction.response.send_message(f"`{task_name}` は既に存在します。", ephemeral=True)
                    return

                overwrites = {
                    guild.default_role: PermissionOverwrite(view_channel=False),
                    role: PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                    guild.me: PermissionOverwrite(view_channel=True),
                    interaction.user: PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),  # 作成者を追加
                }

                category = discord.utils.get(guild.categories, name="タスク")

                # チャンネル作成
                channel: TextChannel = await guild.create_text_channel(
                    name=task_name,
                    overwrites=overwrites,
                    category=category
                )

                await select_interaction.response.send_message(
                    f"✅ タスクチャンネル {channel.mention} を作成しました（ロール: {role.mention}）",
                    ephemeral=True
                )

        class RoleSelectView(ui.View):
            def __init__(self):
                super().__init__(timeout=60)
                self.add_item(RoleSelect())

        await interaction.response.send_message("🎯 次にロールを選択してください：", view=RoleSelectView(), ephemeral=True)

# スラッシュコマンド登録
async def setup(bot: commands.Bot):
    @app_commands.command(name="create_tasks", description="[管理者サーバー向け] ウィザード形式でタスクチャンネルを作成")
    @app_commands.guilds(discord.Object(id=ADMIN_GUILD_ID))
    async def create_tasks(interaction: Interaction):
        await interaction.response.send_modal(TaskNameModal(bot, interaction))

    bot.tree.add_command(create_tasks)