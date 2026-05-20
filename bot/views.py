import discord


class BuyConfirmView(discord.ui.View):
    """매수 실행 전 확인 버튼."""

    def __init__(self, owner_id: int, on_confirm) -> None:
        super().__init__(timeout=30.0)
        self._owner_id = owner_id
        self._on_confirm = on_confirm

    async def _is_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._owner_id:
            await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
            return False
        return True

    def _disable_all(self) -> None:
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="✅ 매수 진행", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._is_owner(interaction):
            return
        self._disable_all()
        await interaction.response.edit_message(view=self)
        await self._on_confirm(interaction)
        self.stop()

    @discord.ui.button(label="❌ 취소", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._is_owner(interaction):
            return
        self._disable_all()
        await interaction.response.edit_message(content="취소되었습니다.", view=self)
        self.stop()
