import discord
from discord import app_commands

from bot.commands import account, buy, dividend, portfolio, unexecuted
from core.config import Settings
from core.logger import get_logger
from kiwoom.client import KiwoomClient

log = get_logger(__name__)


class IsaflowBot(discord.Client):
    def __init__(self, settings: Settings, kiwoom: KiwoomClient) -> None:
        super().__init__(intents=discord.Intents.default())
        self.settings = settings
        self.kiwoom = kiwoom
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        account.register(self.tree, self.settings, self.kiwoom)
        buy.register(self.tree, self.settings, self.kiwoom)
        portfolio.register(self.tree, self.settings, self.kiwoom)
        unexecuted.register(self.tree, self.settings, self.kiwoom)
        dividend.register(self.tree, self.settings, self.kiwoom)
        await self.tree.sync()
        log.info("슬래시 커맨드 동기화 완료")

    async def on_ready(self) -> None:
        log.info("봇 로그인: %s", self.user)
