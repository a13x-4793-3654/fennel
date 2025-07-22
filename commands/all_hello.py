from discord import app_commands, Interaction
from discord.ext import commands

async def setup(bot: commands.Bot):
    # スラッシュコマンドを登録
    @app_commands.command(name="hello", description="挨拶を返します")
    async def hello(interaction: Interaction):
        await interaction.response.send_message(f"こんにちは、{interaction.user.display_name}！")

    bot.tree.add_command(hello)