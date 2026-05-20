"""/미체결 — 매수 미체결 주문을 최신 매도1호가로 재주문.

조회 시점과 주문 시점 사이 호가가 변해 미체결로 남은 주문을,
기존 주문을 취소하고 최신 매도1호가로 다시 주문해 마무리한다.
"""
import discord
from discord import app_commands

from bot.permissions import check_access
from bot.views import BuyConfirmView
from core.config import Settings
from core.logger import get_logger
from kiwoom.client import KiwoomClient

log = get_logger(__name__)


def register(tree: app_commands.CommandTree, settings: Settings, kiwoom: KiwoomClient) -> None:
    @tree.command(name="미체결", description="매수 미체결 주문을 최신 매도1호가로 재주문")
    async def _cmd(interaction: discord.Interaction) -> None:
        if not await check_access(interaction, settings):
            return

        await interaction.response.defer(thinking=True)

        # 1) 미체결 내역 조회
        try:
            orders = await kiwoom.get_unexecuted_orders()
        except Exception as e:
            log.exception("미체결 조회 실패")
            await interaction.followup.send(f"❌ 미체결 조회 실패: {e}", ephemeral=True)
            return

        if not orders:
            await interaction.followup.send("✅ 미체결 주문이 없습니다.")
            return

        # 2) 각 미체결 종목의 최신 매도1호가 조회
        try:
            repriced = []  # [(UnexecutedOrder, 최신매도1호가), ...]
            for o in orders:
                ask = await kiwoom.get_ask_price(o.ticker)
                repriced.append((o, ask))
        except Exception as e:
            log.exception("매도1호가 조회 실패")
            await interaction.followup.send(f"❌ 시세 조회 실패: {e}", ephemeral=True)
            return

        # 3) 확인 임베드 — 기존가 → 새 가격 비교
        lines = []
        for o, ask in repriced:
            if ask <= 0:
                lines.append(
                    f"• **{o.name}** ({o.ticker})\n"
                    f"   {o.unexec_qty}주 — ⏳ 호가 없음, 재주문 제외"
                )
            else:
                diff = ask - o.order_price
                sign = f"({diff:+,}원)" if diff else "(동일)"
                lines.append(
                    f"• **{o.name}** ({o.ticker})\n"
                    f"   {o.unexec_qty}주 — {o.order_price:,}원 → {ask:,}원 {sign}"
                )

        embed = discord.Embed(
            title="🔄 미체결 재주문 확인",
            description="\n".join(lines),
            color=0xFFA000,
        )
        embed.set_footer(text="기존 미체결 주문을 취소하고 최신 매도1호가로 다시 주문합니다.")

        # 재주문 가능한 항목 (호가 정상)
        actionable = [(o, ask) for o, ask in repriced if ask > 0]
        if not actionable:
            await interaction.followup.send(embed=embed)
            return

        # 4) 확인 → 취소 후 재주문
        async def on_confirm(inter: discord.Interaction) -> None:
            results = []
            for o, ask in actionable:
                try:
                    # 4-1) 기존 미체결 주문 취소
                    await kiwoom.cancel_order(o.order_no, o.ticker)
                except Exception as e:
                    log.exception("주문 취소 실패: %s", o.ticker)
                    results.append(f"❌ {o.name}: 취소 실패 — {e} (재주문 안 함)")
                    continue
                try:
                    # 4-2) 최신 매도1호가로 재주문
                    new_no = await kiwoom.buy_limit(o.ticker, o.unexec_qty, ask)
                    results.append(
                        f"✅ {o.name} {o.unexec_qty}주 @ {ask:,}원 (재주문 {new_no})"
                    )
                except Exception as e:
                    log.exception("재주문 실패: %s", o.ticker)
                    results.append(
                        f"⚠️ {o.name}: 기존 주문은 취소됐으나 재주문 실패 — {e}"
                    )

            result_embed = discord.Embed(
                title="🔄 재주문 결과",
                description="\n".join(results),
                color=0x66BB6A,
            )
            await inter.followup.send(embed=result_embed)

        view = BuyConfirmView(owner_id=settings.discord_owner_id, on_confirm=on_confirm)
        await interaction.followup.send(embed=embed, view=view)
