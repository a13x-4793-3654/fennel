from config import ADMIN_GUILD_ID
from discord import app_commands, Interaction, TextChannel, PermissionOverwrite
from discord.ext import commands
from discord import ui
import discord

# 🔹 確認ボタンのビュー
class ConfirmCloseView(ui.View):
    def __init__(self, channel: TextChannel):
        super().__init__(timeout=30)
        self.channel = channel

    @ui.button(label="✅ はい、閉じる", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: Interaction, button: ui.Button):
        guild = interaction.guild
        overwrites = self.channel.overwrites

        # 書き込み権限を削除
        for target, perms in overwrites.items():
            if isinstance(perms, PermissionOverwrite):
                perms.send_messages = False
                overwrites[target] = perms

        # チャンネル名を変更（[完了]接頭辞）
        original_name = self.channel.name
        if not original_name.startswith("Closed-"):
            new_name = f"Closed-{original_name}"
            await self.channel.edit(name=new_name)

        # 権限を適用
        await self.channel.edit(overwrites=overwrites)

        # ✅ 完了カテゴリーへ移動（なければ作成）
        archive_category = discord.utils.get(guild.categories, name="完了済みタスク")
        if not archive_category:
            archive_category = await guild.create_category("完了済みタスク")

        await self.channel.edit(category=archive_category)

        # 完了メッセージ
        await interaction.response.send_message("📦 このタスクはアーカイブされました。", ephemeral=False)

        # ボタンを無効化
        message = await interaction.original_response()
        for item in self.children:
            item.disabled = True
        await message.edit(view=self)

# 🔹 スラッシュコマンドの登録
async def setup(bot: commands.Bot):
    @app_commands.command(name="close_task", description="[管理者サーバー向け] このタスクをアーカイブとして閉じます")
    @app_commands.guilds(discord.Object(id=ADMIN_GUILD_ID))
    async def close_task(interaction: Interaction):
        channel = interaction.channel
        if not isinstance(channel, TextChannel):
            await interaction.response.send_message("❌ このコマンドはテキストチャンネル内でのみ使用できます。", ephemeral=True)
            return

        category = channel.category
        if not category or category.name.strip() != "タスク":
            await interaction.response.send_message("❌ このチャンネルは、タスク カテゴリーに属していません。", ephemeral=True)
            return

        # 確認ビューを送信
        view = ConfirmCloseView(channel)
        await interaction.response.send_message("本当にこのタスクを閉じてアーカイブしますか？", view=view, ephemeral=True)

    bot.tree.add_command(close_task)