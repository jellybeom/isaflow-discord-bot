import discord

from core.config import Settings


async def check_access(interaction: discord.Interaction, settings: Settings) -> bool:
    """본인 + 허용된 채널에서만 통과. 거부 시 메시지 보내고 False 반환."""
    if interaction.user.id != settings.discord_owner_id:
        await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
        return False
    if interaction.channel_id != settings.discord_allowed_channel_id:
        await interaction.response.send_message(
            "❌ 이 채널에서는 사용할 수 없습니다.", ephemeral=True
        )
        return False
    return True
